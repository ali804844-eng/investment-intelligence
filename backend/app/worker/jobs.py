"""Background job handlers. Each handler logs its steps into JobRun.log so runs are inspectable."""
from __future__ import annotations

import time
from collections.abc import Callable
from datetime import date, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.screening.engine import run_screen
from app.core.screening.strategies import STRATEGIES
from app.db import utcnow
from app.models import (
    AgentMemory,
    AgentSettings,
    ApiUsage,
    Filing,
    FundamentalFact,
    JobRun,
    Portfolio,
    ProviderStatus,
    ResearchCandidate,
    ResearchReport,
    ResearchRun,
    Security,
    StrategyVersion,
    Transaction,
    User,
    Watchlist,
    WatchlistItem,
)
from app.providers.registry import get_registry
from app.providers.sample import last_business_day
from app.services import learning, market_data as md, portfolio as pfs, research
from app.services.alerts import evaluate_alerts
from app.services.notifications import notify
from app.services.panel import load_panel
from app.services.regime import market_regime


class JobCancelled(Exception):
    pass


class JobTimeout(Exception):
    pass


class JobContext:
    def __init__(self, db: Session, jr: JobRun, heartbeat: Callable[[list], bool] | None = None):
        self.db = db
        self.jr = jr
        self.user = db.get(User, jr.user_id) if jr.user_id else None
        self.started = time.monotonic()
        self._heartbeat = heartbeat
        self.steps: list[dict] = []

    def log(self, step: str, message: str, data: Any = None, level: str = "info") -> None:
        entry = {"t": utcnow().isoformat(), "step": step, "level": level, "message": message[:1000]}
        if data is not None:
            entry["data"] = research._jsonable(data)
        self.steps.append(entry)
        self.check(push_log=True)

    def check(self, push_log: bool = False) -> None:
        """Cooperative timeout/cancellation point. The JobRun row is only written through the heartbeat session so
        the job's own session never holds a lock on it (avoids self-deadlock)."""
        if time.monotonic() - self.started > self.jr.timeout_seconds:
            raise JobTimeout(f"job exceeded its timeout of {self.jr.timeout_seconds}s")
        if self._heartbeat is not None and not self._heartbeat(list(self.steps) if push_log else None):
            raise JobCancelled("job was cancelled")


# ------------------------------------------------------------------ helpers
def tracked_securities(db: Session, user_id: int | None) -> list[Security]:
    """Universe the agent maintains: sample universe (if enabled) + real securities the user watches or holds +
    benchmarks."""
    ids: set[int] = set()
    reg = get_registry()
    if reg.sample_enabled:
        ids |= set(db.scalars(select(Security.id).where(Security.is_sample.is_(True))).all())
    if user_id:
        ids |= set(db.scalars(select(WatchlistItem.security_id).join(Watchlist).where(Watchlist.user_id == user_id)).all())
        ids |= set(db.scalars(select(Transaction.security_id).join(Portfolio).where(Portfolio.user_id == user_id,
                                                                                   Transaction.security_id.is_not(None))).all())
    else:
        ids |= set(db.scalars(select(WatchlistItem.security_id)).all())
        ids |= set(db.scalars(select(Transaction.security_id).where(Transaction.security_id.is_not(None))).all())
    ids |= set(db.scalars(select(Security.id).where(Security.symbol.in_(["SPY", "VOO", "IVV", "VTI"]),
                                                    Security.is_sample.is_(False))).all())
    return list(db.scalars(select(Security).where(Security.id.in_(ids), Security.is_active.is_(True))).all()) if ids else []


def watched_ids(db: Session, user_id: int) -> set[int]:
    ids = set(db.scalars(select(WatchlistItem.security_id).join(Watchlist).where(Watchlist.user_id == user_id)).all())
    ids |= set(db.scalars(select(Transaction.security_id).join(Portfolio).where(Portfolio.user_id == user_id,
                                                                               Transaction.security_id.is_not(None))).all())
    return ids


def provider_calls_today(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(ApiUsage.calls), 0)).where(ApiUsage.day == utcnow().date(),
                                                                                ApiUsage.provider != "anthropic")) or 0)


def _refresh(ctx: JobContext, secs: list[Security], fundamentals: bool = True) -> dict:
    db = ctx.db
    settings = db.get(AgentSettings, ctx.user.id) if ctx.user else None
    budget = settings.daily_provider_call_budget if settings else 500
    stats = {"prices_ok": 0, "prices_failed": 0, "fund_ok": 0, "fund_failed": 0, "skipped_budget": 0, "errors": []}
    week_ago = utcnow() - timedelta(days=7)
    for s in secs:
        if not s.is_sample and provider_calls_today(db) >= budget:
            stats["skipped_budget"] += 1
            continue
        try:
            md.refresh_prices(db, s)
            stats["prices_ok"] += 1
        except md.DataUnavailable as e:
            stats["prices_failed"] += 1
            stats["errors"].append(f"{s.symbol} prices: {str(e)[:160]}")
        if fundamentals and s.asset_class == "equity":
            last = db.scalar(select(func.max(FundamentalFact.fetched_at)).where(FundamentalFact.security_id == s.id))
            if last is None or last < week_ago:
                try:
                    md.refresh_fundamentals(db, s)
                    stats["fund_ok"] += 1
                except md.DataUnavailable as e:
                    stats["fund_failed"] += 1
                    stats["errors"].append(f"{s.symbol} fundamentals: {str(e)[:160]}")
        db.commit()
        ctx.check()
    return stats


# ------------------------------------------------------------------ handlers
def job_data_refresh(ctx: JobContext) -> dict:
    secs = tracked_securities(ctx.db, ctx.jr.user_id)
    ctx.log("universe", f"{len(secs)} tracked securities")
    stats = _refresh(ctx, secs)
    ctx.log("prices", f"prices ok={stats['prices_ok']} failed={stats['prices_failed']}; fundamentals ok={stats['fund_ok']} "
                      f"failed={stats['fund_failed']}; skipped (budget)={stats['skipped_budget']}", stats["errors"][:20])
    macro = md.refresh_macro(ctx.db)
    ctx.log("macro", "macro refresh", macro)
    fx = md.refresh_fx(ctx.db)
    ctx.log("fx", "fx refresh", fx)
    ctx.db.commit()
    if secs and stats["prices_ok"] == 0:
        raise RuntimeError("No price refresh succeeded: " + "; ".join(stats["errors"][:3]))
    return stats | {"macro": {k: v.get("status") for k, v in macro.items()}}


def job_market_review(ctx: JobContext) -> dict:
    db = ctx.db
    bench = [s for s in tracked_securities(db, ctx.jr.user_id) if s.asset_class in ("index", "etf", "bond", "commodity", "fx")]
    stats = _refresh(ctx, bench, fundamentals=False)
    ctx.log("refresh", f"refreshed {stats['prices_ok']} benchmark/macro instruments", stats["errors"][:10])
    md.refresh_macro(db)
    reg = market_regime(db)
    ctx.log("regime", f"market regime: {reg['label']}", {"explanation": reg["explanation"], "quality": reg["data_quality"]})
    moves = []
    if ctx.user:
        for sid in watched_ids(db, ctx.user.id):
            s = db.get(Security, sid)
            q = md.get_quote(db, s)
            if q.get("change_pct") is not None and abs(q["change_pct"]) >= 0.05 and q["freshness"]["status"] == "fresh":
                moves.append({"symbol": s.symbol, "change": q["change_pct"], "sample": s.is_sample})
                notify(db, ctx.user.id, f"{s.symbol} moved {q['change_pct']:+.1%}{' [SAMPLE]' if s.is_sample else ''}",
                       f"Close-to-close as of {q['as_of']} ({q['quality']}).", "warning", "market",
                       link=f"/research/{s.symbol}", dedupe_key=f"move:{s.id}:{q['as_of']}")
        prev = db.scalar(select(AgentMemory).where(AgentMemory.user_id == ctx.user.id, AgentMemory.kind == "fact",
                                                   AgentMemory.content.like("Market regime:%")).order_by(AgentMemory.created_at.desc()))
        cur_txt = f"Market regime: {reg['label']} ({(reg.get('benchmark') or {}).get('symbol')}, {reg.get('as_of')})"
        if prev is None or not prev.content.startswith(f"Market regime: {reg['label']} "):
            db.add(AgentMemory(user_id=ctx.user.id, kind="fact", content=cur_txt, tags=["regime"]))
            if prev is not None:
                notify(db, ctx.user.id, f"Market regime changed to '{reg['label']}'", "\n".join(reg["explanation"]), "info",
                       "market", "/", dedupe_key=f"regime:{reg['label']}:{reg.get('as_of')}")
    db.commit()
    return {"regime": reg["label"], "large_moves": moves}


def job_watchlist_review(ctx: JobContext) -> dict:
    db = ctx.db
    if not ctx.user:
        return {"skipped": "no user"}
    secs = [db.get(Security, i) for i in watched_ids(db, ctx.user.id)]
    stats = _refresh(ctx, [s for s in secs if s], fundamentals=False)
    ctx.log("refresh", f"refreshed {stats['prices_ok']} watched/held securities", stats["errors"][:10])
    alerts = evaluate_alerts(db, ctx.user.id)
    ctx.log("alerts", f"{len(alerts['fired'])} alert(s) fired", alerts)
    breaches = {}
    for pf in db.scalars(select(Portfolio).where(Portfolio.user_id == ctx.user.id)).all():
        try:
            pfs.write_snapshot(db, pf)
            rr = pfs.risk_report(db, pf, ctx.user.id)
        except Exception as e:  # noqa: BLE001
            ctx.log("risk", f"portfolio {pf.id} risk evaluation failed: {e}", level="error")
            continue
        if rr["breaches"]:
            breaches[pf.name] = [c["message"] for c in rr["breaches"]]
            notify(db, ctx.user.id, f"Risk limit breach in '{pf.name}'", "\n".join(breaches[pf.name][:8]), "critical", "risk",
                   f"/portfolios/{pf.id}", dedupe_key=f"breach:{pf.id}:{'|'.join(sorted(breaches[pf.name]))[:150]}")
    ctx.log("risk", f"{len(breaches)} portfolio(s) with limit breaches", breaches)
    db.commit()
    return {"alerts_fired": len(alerts["fired"]), "breaches": breaches}


def job_filings_monitor(ctx: JobContext) -> dict:
    db = ctx.db
    if not ctx.user:
        return {"skipped": "no user"}
    ids = watched_ids(db, ctx.user.id)
    new_total = 0
    for sid in ids:
        s = db.get(Security, sid)
        if s.asset_class != "equity":
            continue
        try:
            r = md.refresh_filings(db, s)
            md.refresh_events(db, s)
            md.refresh_news(db, s)
        except (md.DataUnavailable, Exception) as e:  # noqa: BLE001
            ctx.log("filings", f"{s.symbol}: {str(e)[:200]}", level="warning")
            continue
        material = [f for f in r.get("new", []) if f["form"] in ("10-K", "10-Q", "8-K", "20-F", "6-K", "ANNUAL (sample)")]
        new_total += len(r.get("new", []))
        for f in material[:3]:
            notify(db, ctx.user.id, f"New {f['form']} for {s.symbol}{' [SAMPLE]' if s.is_sample else ''}",
                   f"Filed {f['filed_date']} (accession {f['accession']}).", "info", "filing", f"/research/{s.symbol}",
                   dedupe_key=f"filing:{s.id}:{f['accession']}", dedupe_hours=24 * 365)
        db.commit()
        ctx.check()
    ctx.log("filings", f"{new_total} new filing records across {len(ids)} watched securities")
    return {"new_filings": new_total, "watched": len(ids)}


def _universe_sample(db: Session) -> bool:
    return db.scalar(select(Security.id).where(Security.is_sample.is_(False), Security.asset_class == "equity").limit(1)) is None


def job_screening(ctx: JobContext, run: ResearchRun | None = None) -> dict:
    db = ctx.db
    if not ctx.user:
        return {"skipped": "no user"}
    keys = ctx.jr.params.get("strategies") or list(STRATEGIES)
    sample = _universe_sample(db)
    panel = load_panel(db, include_sample=sample)
    asof = last_business_day()
    if run is None:
        run = ResearchRun(user_id=ctx.user.id, job_run_id=ctx.jr.id, kind="screen")
        db.add(run)
        db.flush()
    out = {}
    evmap = {}
    for sid in panel.securities:
        evs = research.upcoming_events(db, sid)
        if evs:
            evmap[sid] = evs
    for key in keys:
        v = learning.production_version(db, ctx.user.id, key)
        res = run_screen(panel, asof, v.params, events=evmap)
        for c in res["candidates"][: int(v.params.get("top_n", 8))]:
            db.add(ResearchCandidate(user_id=ctx.user.id, run_id=run.id, security_id=c["security_id"], strategy_key=key,
                                     strategy_version=v.version, horizon=STRATEGIES[key]["horizon"], score=c["score"],
                                     rank=c["rank"], explanation=research._jsonable(c),
                                     data_quality="sample" if c["is_sample"] else "eod"))
        out[key] = [c["symbol"] for c in res["candidates"][:8]]
        ctx.log("screen", f"{key} v{v.version}: {res['n_candidates']} candidates of {res['n_evaluated']}", out[key])
    db.commit()
    return {"run_id": run.id, "universe": "sample" if sample else "real", "candidates": out}


def job_research_cycle(ctx: JobContext) -> dict:
    db = ctx.db
    if not ctx.user:
        return {"skipped": "no user"}
    uid = ctx.user.id
    settings = db.get(AgentSettings, uid)
    run = ResearchRun(user_id=uid, job_run_id=ctx.jr.id, kind="research_cycle")
    db.add(run)
    db.flush()
    # 1. priorities
    watched = watched_ids(db, uid)
    ctx.log("1_priorities", f"{len(watched)} watched/held securities; strategies: {', '.join(STRATEGIES)}",
            {"provider_calls_today": provider_calls_today(db)})
    # 2. retrieve
    secs = tracked_securities(db, uid)
    stats = _refresh(ctx, secs)
    md.refresh_macro(db)
    md.refresh_fx(db)
    ctx.log("2_retrieve", f"prices ok={stats['prices_ok']} failed={stats['prices_failed']}", stats["errors"][:10])
    # 3. validate
    stale, missing = [], []
    for s in secs:
        q = md.get_quote(db, s)
        if q["price"] is None:
            missing.append(s.symbol)
        elif q["freshness"]["status"] != "fresh":
            stale.append(s.symbol)
    bad_prov = [p.provider for p in db.scalars(select(ProviderStatus).where(ProviderStatus.status == "degraded")).all()]
    ctx.log("3_validate", f"{len(stale)} stale, {len(missing)} missing prices; degraded providers: {bad_prov or 'none'}",
            {"stale": stale[:30], "missing": missing[:30]})
    # 4. detect changes
    since = utcnow() - timedelta(days=2)
    new_filings = db.scalars(select(Filing).where(Filing.security_id.in_(watched or {-1}), Filing.fetched_at >= since)).all()
    moves = []
    for sid in watched:
        q = md.get_quote(db, db.get(Security, sid))
        if q.get("change_pct") is not None and abs(q["change_pct"]) >= 0.05:
            moves.append((q["symbol"], q["change_pct"]))
    ctx.log("4_changes", f"{len(new_filings)} new filings, {len(moves)} large moves in watched names", {"moves": moves})
    # 5. screen
    scr = job_screening(ctx, run)
    # 6-7. analyse top candidates (bear cases & uncertainty are part of each report)
    max_reports = settings.max_reports_per_cycle if settings else 3
    week_ago = utcnow() - timedelta(days=7)
    cands = db.scalars(select(ResearchCandidate).where(ResearchCandidate.run_id == run.id)
                       .order_by(ResearchCandidate.rank, ResearchCandidate.score.desc())).all()
    reported, seen = [], set()
    for c in cands:
        if len(reported) >= max_reports:
            break
        if c.security_id in seen:
            continue
        seen.add(c.security_id)
        recent = db.scalar(select(ResearchReport.id).where(ResearchReport.user_id == uid, ResearchReport.security_id == c.security_id,
                                                           ResearchReport.created_at >= week_ago).limit(1))
        if recent:
            continue
        s = db.get(Security, c.security_id)
        prev = db.scalars(select(ResearchReport).where(ResearchReport.user_id == uid, ResearchReport.security_id == s.id)
                          .order_by(ResearchReport.created_at.desc()).limit(1)).first()
        pf = db.scalars(select(Portfolio).where(Portfolio.user_id == uid, Portfolio.is_hypothetical.is_(True))
                        .order_by(Portfolio.id)).first()
        rep = research.generate_report(db, uid, s, c.horizon, c.strategy_key, pf.id if pf else None, created_by="agent")
        reported.append({"symbol": s.symbol, "report_id": rep.id, "conclusion": rep.conclusion,
                         "previous": prev.conclusion if prev else None})
        db.commit()
        ctx.check()
    ctx.log("6_analyse", f"{len(reported)} new research reports", reported)
    ctx.log("7_downside", "Bear-case valuation, risk flags and invalidation conditions recorded in each report")
    # 8. portfolio exposure
    breaches = {}
    for pf in db.scalars(select(Portfolio).where(Portfolio.user_id == uid)).all():
        try:
            rr = pfs.risk_report(db, pf, uid)
            if rr["breaches"]:
                breaches[pf.name] = len(rr["breaches"])
        except Exception as e:  # noqa: BLE001
            ctx.log("8_portfolio", f"risk report failed for {pf.name}: {e}", level="warning")
    ctx.log("8_portfolio", f"portfolios with breaches: {breaches or 'none'}")
    # 9-10. store & rank the research queue
    queue = []
    bonus = {"consider": 1.0, "monitor": 0.2, "insufficient_evidence": -0.5, "reject": -1.0}
    rep_by_sid = {r["symbol"]: r for r in reported}
    for c in cands:
        s = db.get(Security, c.security_id)
        r = rep_by_sid.get(s.symbol)
        queue.append({"symbol": s.symbol, "strategy": c.strategy_key, "score": c.score,
                      "priority": c.score + (bonus.get(r["conclusion"], 0) if r else 0), "report": r})
    queue.sort(key=lambda x: -x["priority"])
    run.summary = research._jsonable({"queue": queue[:25], "reports": reported, "stale": stale[:20], "moves": moves,
                                      "breaches": breaches, "universe": scr.get("universe")})
    run.steps = list(ctx.steps)
    db.add(AgentMemory(user_id=uid, kind="research", ref_type="research_run", ref_id=run.id, tags=["research_cycle"],
                       content=f"Research cycle {run.id} on {last_business_day()}: top queue "
                               + ", ".join(f"{q['symbol']}({q['strategy']})" for q in queue[:6])
                               + ("; new reports: " + ", ".join(f"{r['symbol']}→{r['conclusion']}" for r in reported) if reported else "")))
    ctx.log("9_store", f"stored run #{run.id} with {len(cands)} candidates")
    ctx.log("10_rank", "ranked research queue", queue[:10])
    # 11. notify on material findings only
    material = [r for r in reported if r["conclusion"] == "consider" or (r["previous"] and r["previous"] != r["conclusion"])]
    if material:
        notify(db, uid, f"Research cycle: {len(material)} material finding(s)",
               "\n".join(f"- {r['symbol']}: {r['previous'] + ' → ' if r['previous'] else ''}{r['conclusion']} (report #{r['report_id']})"
                         for r in material), "info", "research", "/research", dedupe_key=f"cycle:{run.id}")
    ctx.log("11_notify", f"{len(material)} material finding(s) notified")
    # 12. outcomes
    ev = learning.evaluate_due_predictions(db, uid)
    ctx.log("12_outcomes", f"{len(ev['evaluated'])} predictions evaluated", ev)
    db.commit()
    return {"run_id": run.id, "reports": reported, "queue_top": queue[:10], "evaluated_predictions": len(ev["evaluated"])}


def job_prediction_evaluation(ctx: JobContext) -> dict:
    ev = learning.evaluate_due_predictions(ctx.db, ctx.jr.user_id)
    ctx.log("evaluate", f"{len(ev['evaluated'])} evaluated, {len(ev['skipped'])} skipped", ev)
    ctx.db.commit()
    return ev


def job_performance_review(ctx: JobContext) -> dict:
    db = ctx.db
    if not ctx.user:
        return {"skipped": "no user"}
    lines = []
    for pf in db.scalars(select(Portfolio).where(Portfolio.user_id == ctx.user.id)).all():
        pfs.write_snapshot(db, pf)
        h = pfs.portfolio_history(db, pf)
        m = h["metrics"]
        if m:
            w = h["periods"].get("1M")
            lines.append(f"- {pf.name}{' (hypothetical)' if pf.is_hypothetical else ''}: 1M {w:+.1%}" if w is not None else f"- {pf.name}: n/a")
    sc = learning.prediction_scorecard(db, ctx.user.id)["overall"]
    lines.append(f"- Predictions evaluated: {sc.get('n', 0)}" + (f", hit rate {sc['hit_rate']:.0%}, Brier {sc['brier']:.3f}" if sc.get("n") else ""))
    ctx.log("review", "weekly review", lines)
    notify(db, ctx.user.id, "Weekly performance & prediction review", "\n".join(lines), "info", "review", "/learning",
           dedupe_key=f"weekly:{last_business_day().isocalendar()[1]}")
    db.commit()
    return {"lines": lines}


def job_strategy_validation(ctx: JobContext) -> dict:
    db = ctx.db
    if not ctx.user:
        return {"skipped": "no user"}
    end = last_business_day()
    start = date(end.year - 7, end.month, 1)
    out = {}
    keys = ctx.jr.params.get("strategies") or list(STRATEGIES)
    for key in keys:
        try:
            bt = learning.run_backtest_job(db, ctx.user.id, key, None, start, end, method="walk_forward")
        except ValueError as e:
            ctx.log("validate", f"{key}: {e}", level="warning")
            continue
        m = bt.metrics
        weak = (m.get("sharpe") is not None and m["sharpe"] < 0.2) or (m.get("n_folds") and m.get("folds_beating_benchmark", 0) / m["n_folds"] < 0.4)
        out[key] = {"backtest_id": bt.id, "oos_sharpe": m.get("sharpe"), "excess_cagr": m.get("excess_cagr"),
                    "folds_beating": f"{m.get('folds_beating_benchmark')}/{m.get('n_folds')}", "weak": bool(weak)}
        ctx.log("validate", f"{key}: OOS Sharpe {m.get('sharpe')}, folds beating benchmark {out[key]['folds_beating']}", out[key])
        db.commit()
        if weak and ctx.jr.params.get("propose", True):
            pending = db.scalar(select(StrategyVersion.id).where(StrategyVersion.user_id == ctx.user.id, StrategyVersion.strategy_key == key,
                                                                 StrategyVersion.status == "candidate").limit(1))
            if not pending:
                base = learning.production_version(db, ctx.user.id, key)
                tn = int(base.params.get("top_n", 8))
                cand = learning.propose_version(db, ctx.user.id, key, {"top_n": min(tn * 2, 20)},
                                                notes=f"Auto-proposed after weak walk-forward validation (backtest #{bt.id}): "
                                                      "broaden the portfolio to reduce idiosyncratic noise.", proposed_by="agent")
                exp = learning.run_experiment(db, ctx.user.id, cand.id, start, end)
                ctx.log("propose", f"{key}: proposed v{cand.version}; experiment #{exp.id} {exp.status}")
                notify(db, ctx.user.id, f"Strategy '{key}' candidate v{cand.version}: experiment {exp.status}",
                       "Review it in the Learning Lab. Promotion requires your approval.", "info", "learning", "/learning",
                       dedupe_key=f"exp:{exp.id}")
                db.commit()
        ctx.check()
    return out


def job_backtest(ctx: JobContext) -> dict:
    p = ctx.jr.params
    bt = learning.run_backtest_job(ctx.db, ctx.jr.user_id, p["strategy_key"], p.get("version_id"),
                                   date.fromisoformat(p["start"]), date.fromisoformat(p["end"]), p.get("method", "single"),
                                   p.get("costs"), p.get("grid"))
    ctx.log("backtest", f"backtest #{bt.id} complete", {k: bt.metrics.get(k) for k in ("cagr", "sharpe", "max_drawdown", "excess_cagr")})
    ctx.db.commit()
    return {"backtest_id": bt.id}


def job_experiment(ctx: JobContext) -> dict:
    p = ctx.jr.params
    exp = learning.run_experiment(ctx.db, ctx.jr.user_id, int(p["candidate_version_id"]), date.fromisoformat(p["start"]),
                                  date.fromisoformat(p["end"]), p.get("criteria"))
    ctx.log("experiment", f"experiment #{exp.id}: {exp.status}", exp.results.get("verdict"))
    ctx.db.commit()
    return {"experiment_id": exp.id, "status": exp.status}


def job_report(ctx: JobContext) -> dict:
    p = ctx.jr.params
    s = ctx.db.get(Security, int(p["security_id"]))
    rep = research.generate_report(ctx.db, ctx.jr.user_id, s, p.get("horizon", "long"), p.get("strategy"), p.get("portfolio_id"),
                                   created_by=p.get("created_by", "user"))
    ctx.log("report", f"report #{rep.id}: {rep.conclusion}")
    ctx.db.commit()
    return {"report_id": rep.id}


def job_data_health(ctx: JobContext) -> dict:
    db = ctx.db
    reg = get_registry()
    statuses = {p["name"]: p["configured"] for p in reg.describe()}
    rows = {r.provider: r for r in db.scalars(select(ProviderStatus)).all()}
    degraded = [n for n, r in rows.items() if r.status == "degraded" and statuses.get(n)]
    stale = []
    if ctx.user:
        for sid in watched_ids(db, ctx.user.id):
            s = db.get(Security, sid)
            q = md.get_quote(db, s)
            if q["freshness"]["status"] != "fresh":
                stale.append(s.symbol)
    ctx.log("health", f"degraded providers: {degraded or 'none'}; stale watched prices: {stale or 'none'}")
    if ctx.user and (degraded or stale):
        notify(db, ctx.user.id, "Data feed attention needed",
               (f"Degraded: {', '.join(degraded)}. " if degraded else "") + (f"Stale prices: {', '.join(stale[:15])}." if stale else ""),
               "warning", "data", "/settings", dedupe_key=f"health:{','.join(degraded)}:{','.join(stale[:15])}", dedupe_hours=12)
    db.commit()
    return {"configured": statuses, "degraded": degraded, "stale": stale}


HANDLERS: dict[str, Callable[[JobContext], dict]] = {
    "data_refresh": job_data_refresh,
    "market_review": job_market_review,
    "watchlist_review": job_watchlist_review,
    "filings_monitor": job_filings_monitor,
    "screening": job_screening,
    "research_cycle": job_research_cycle,
    "prediction_evaluation": job_prediction_evaluation,
    "performance_review": job_performance_review,
    "strategy_validation": job_strategy_validation,
    "backtest": job_backtest,
    "experiment": job_experiment,
    "report": job_report,
    "data_health": job_data_health,
}
