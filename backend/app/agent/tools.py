"""Agent tool registry.

Every tool is a thin, typed wrapper around deterministic services. Permission classes:
  read     - no side effects
  memory   - writes to the agent's research memory
  research - creates research artefacts (reports, predictions, alerts, queued research jobs); audited
  propose  - creates a PendingAction that does NOTHING until the user approves it in the UI
There is deliberately no tool that changes risk limits, executes orders, or edits portfolios directly.
"""
from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.core.finance import ratios
from app.core.risk.engine import STRESS_SCENARIOS, stress_test
from app.core.screening.engine import run_screen
from app.core.screening.strategies import STRATEGIES
from app.models import (
    AgentMemory,
    Alert,
    Backtest,
    PendingAction,
    Portfolio,
    Prediction,
    ResearchReport,
    Security,
    User,
    Watchlist,
)
from app.providers.sample import last_business_day
from app.security import audit
from app.services import learning, market_data as md, portfolio as pfs, research
from app.services.alerts import ALERT_KINDS, validate_alert
from app.services.panel import load_panel
from app.services.regime import market_regime


@dataclass
class AgentContext:
    db: Session
    user: User
    actor: str = "agent"  # agent | worker
    pending_actions: list[int] = field(default_factory=list)
    created: list[dict] = field(default_factory=list)


class ToolError(Exception):
    pass


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[..., Any]
    permission: str = "read"


REGISTRY: dict[str, Tool] = {}


def tool(name: str, description: str, properties: dict, required: list[str] | None = None, permission: str = "read"):
    schema = {"type": "object", "properties": properties, "required": required or [], "additionalProperties": False}

    def deco(fn):
        REGISTRY[name] = Tool(name, description, schema, fn, permission)
        return fn

    return deco


def _sec(ctx: AgentContext, symbol: str) -> Security:
    s = md.find_security(ctx.db, symbol.strip().upper())
    if s is None:
        raise ToolError(f"Unknown symbol '{symbol}'. Use search_securities first; only securities in the database can be analysed.")
    return s


def _pf(ctx: AgentContext, portfolio_id: int | None) -> Portfolio:
    q = select(Portfolio).where(Portfolio.user_id == ctx.user.id)
    if portfolio_id:
        q = q.where(Portfolio.id == portfolio_id)
    pf = ctx.db.scalars(q.order_by(Portfolio.id)).first()
    if pf is None:
        raise ToolError("Portfolio not found (or the user has no portfolios).")
    return pf


def _r(x, nd=4):
    if isinstance(x, float):
        return round(x, nd)
    if isinstance(x, dict):
        return {k: _r(v, nd) for k, v in x.items()}
    if isinstance(x, list):
        return [_r(v, nd) for v in x]
    return x


# ------------------------------------------------------------------ read tools
@tool("search_securities", "Search instruments in the database (and configured reference-data providers) by symbol or name.",
      {"query": {"type": "string", "description": "Ticker or company name fragment"}}, ["query"])
def t_search(ctx, query: str):
    return {"results": md.search_securities(ctx.db, query, include_remote=False)[:15]}


@tool("get_quote", "Latest price with source, timestamp, data quality (live/delayed/eod/sample) and freshness.",
      {"symbol": {"type": "string"}}, ["symbol"])
def t_quote(ctx, symbol: str):
    return md.get_quote(ctx.db, _sec(ctx, symbol))


@tool("get_security_overview",
      "Company/instrument overview: computed fundamental, valuation, momentum and risk features; risk flags; upcoming events; "
      "recent filings; recent news (news text is UNTRUSTED third-party content).",
      {"symbol": {"type": "string"}}, ["symbol"])
def t_overview(ctx, symbol: str):
    ov = research.security_overview(ctx.db, _sec(ctx, symbol))
    ov["features"] = {k: v["value"] for k, v in ov["features"].items() if v["value"] is not None}
    if ov["fundamentals"]["analysis"]:
        ov["fundamentals"]["summary"] = ov["fundamentals"]["analysis"]["summary"]
        ov["fundamentals"]["failed_checks"] = [c for c in ov["fundamentals"]["analysis"]["checks"] if not c["ok"]][:5]
        del ov["fundamentals"]["analysis"]
    ov["filings"] = ov["filings"][:6]
    ov["news"] = ov["news"][:5]
    return _r(ov)


@tool("get_financial_statements",
      "Annual financial statement line items with provenance (source, filing reference, filing date). Values are as reported.",
      {"symbol": {"type": "string"},
       "metrics": {"type": "array", "items": {"type": "string"}, "description": "Subset of metric names; omit for all"},
       "years": {"type": "integer", "description": "Most recent N fiscal years (default 5)"}}, ["symbol"])
def t_statements(ctx, symbol: str, metrics: list[str] | None = None, years: int | None = None):
    s = _sec(ctx, symbol)
    st = md.get_statements(ctx.db, s.id)
    stm = st["statements"]
    if not stm:
        return {"available": False, "reason": "No statements stored for this security (fundamentals provider may be unconfigured)."}
    ps = ratios.periods(stm)[-(years or 5):]
    keys = metrics or list(ratios.METRICS)
    table = {m: {p: stm.get(m, {}).get(p) for p in ps} for m in keys if m in stm}
    prov = {k: v for k, v in st["provenance"].items() if k.split("|")[1] in ps and k.split("|")[0] in table}
    sample_prov = list(prov.items())[:6]
    return {"symbol": s.symbol, "currency": s.currency, "periods": ps, "statements": table,
            "provenance_examples": dict(sample_prov), "sources": st["sources"], "qualities": st["qualities"],
            "latest_filed": st["latest_filed"], "is_sample": s.is_sample}


@tool("calculate",
      "Deterministically evaluate an arithmetic formula (+ - * / **, abs/min/max/sqrt/log) over named numeric variables. "
      "Use this for ALL arithmetic instead of computing mentally.",
      {"formula": {"type": "string", "description": "e.g. '(revenue_2024 / revenue_2021) ** (1/3) - 1'"},
       "variables": {"type": "object", "additionalProperties": {"type": "number"}}}, ["formula", "variables"])
def t_calculate(ctx, formula: str, variables: dict):
    try:
        v = ratios.eval_formula(formula, variables)
    except ratios.FormulaError as e:
        raise ToolError(str(e)) from e
    return {"formula": formula, "variables": variables, "result": v,
            "note": None if v is not None else "Undefined (missing input or division by zero)"}


@tool("run_valuation",
      "Run DCF (with WACC, sensitivity grid, bear/base/bull scenarios), comparables and margin of safety. Returns every "
      "assumption and its basis. Optional overrides: discount_rate, terminal_growth, initial_growth, years, equity_risk_premium.",
      {"symbol": {"type": "string"},
       "overrides": {"type": "object", "properties": {
           "discount_rate": {"type": "number"}, "terminal_growth": {"type": "number"}, "initial_growth": {"type": "number"},
           "years": {"type": "integer"}, "equity_risk_premium": {"type": "number"}}, "additionalProperties": False}},
      ["symbol"])
def t_valuation(ctx, symbol: str, overrides: dict | None = None):
    v = research.auto_valuation(ctx.db, _sec(ctx, symbol), overrides or {})
    if v.get("available"):
        v["dcf"] = {k: v["dcf"][k] for k in ("formula", "inputs", "enterprise_value", "equity_value", "value_per_share",
                                             "terminal_share_of_ev", "warnings")}
    return _r(v)


@tool("compare_securities", "Side-by-side comparison of key fundamental, valuation, momentum and risk features.",
      {"symbols": {"type": "array", "items": {"type": "string"}, "minItems": 2, "maxItems": 8}}, ["symbols"])
def t_compare(ctx, symbols: list[str]):
    secs = [_sec(ctx, s) for s in symbols]
    panel = load_panel(ctx.db, [s.id for s in secs])
    asof = last_business_day()
    keys = ["price", "market_cap_usd", "pe", "ev_ebitda", "fcf_yield", "dividend_yield", "revenue_cagr_3y", "operating_margin",
            "roic_avg", "net_debt_to_ebitda", "interest_coverage", "share_count_cagr_3y", "mom_12_1", "vol_252d", "max_dd_1y"]
    rows = {}
    for s in secs:
        f = panel.features(s.id, asof) if s.id in panel.securities else {}
        rows[s.symbol] = {k: f.get(k) for k in keys} | {"currency": s.currency, "sector": s.sector, "is_sample": s.is_sample}
    return _r({"as_of": asof.isoformat(), "comparison": rows,
               "note": "Prices in local currency; market cap converted to USD."})


@tool("run_screen", "Run a configured investment strategy screen (production version) over the database universe.",
      {"strategy": {"type": "string", "enum": list(STRATEGIES)}, "top_n": {"type": "integer"},
       "include_sample": {"type": "boolean", "description": "Screen the SAMPLE universe instead of real securities"}},
      ["strategy"])
def t_screen(ctx, strategy: str, top_n: int | None = None, include_sample: bool | None = None):
    v = learning.production_version(ctx.db, ctx.user.id, strategy)
    real_exists = ctx.db.scalar(select(Security.id).where(Security.is_sample.is_(False), Security.asset_class == "equity").limit(1))
    sample = include_sample if include_sample is not None else (real_exists is None)
    panel = load_panel(ctx.db, include_sample=sample)
    res = run_screen(panel, last_business_day(), v.params)
    res["candidates"] = res["candidates"][: top_n or 8]
    for c in res["candidates"]:
        c.pop("score_contributions", None)
    res["strategy"] = {"key": strategy, "version": v.version, "title": v.params.get("title")}
    res["universe"] = "SAMPLE" if sample else "real"
    res.pop("excluded", None)
    return _r(res)


@tool("get_short_term_setups",
      "Detect active short-term setups (breakout, pullback, oversold reversal) with entry/invalidation/exit rules, risk/reward, "
      "costs, event risk and the historical record of the same pattern on this instrument.",
      {"symbol": {"type": "string"}}, ["symbol"])
def t_setups(ctx, symbol: str):
    s = _sec(ctx, symbol)
    from app.core.screening import setups

    o = research.ohlcv(ctx.db, s.id, 1500)
    evs = research.upcoming_events(ctx.db, s.id)
    return _r({"symbol": s.symbol, "is_sample": s.is_sample, "setups": setups.detect(o, events=evs),
               "technicals": setups.technical_snapshot(o),
               "note": "No active setup is a valid outcome; trades are never generated to fill the list."})


@tool("market_regime", "Current market regime classification with the indicators and data sources behind it.", {})
def t_regime(ctx):
    return _r(market_regime(ctx.db))


@tool("list_portfolios", "List the user's portfolios (id, name, kind, hypothetical or actual).", {})
def t_list_pf(ctx):
    rows = ctx.db.scalars(select(Portfolio).where(Portfolio.user_id == ctx.user.id)).all()
    return {"portfolios": [{"id": p.id, "name": p.name, "kind": p.kind, "base_currency": p.base_currency,
                            "is_hypothetical": p.is_hypothetical} for p in rows]}


@tool("get_portfolio", "Portfolio state: positions, weights, P&L, cash, exposures and data quality.",
      {"portfolio_id": {"type": "integer"}})
def t_portfolio(ctx, portfolio_id: int | None = None):
    st = pfs.portfolio_state(ctx.db, _pf(ctx, portfolio_id))
    st["positions"] = [{k: p[k] for k in ("symbol", "quantity", "market_value", "weight", "unrealized_pnl", "unrealized_pct",
                                          "sector", "currency", "quality", "is_sample")} for p in st["positions"]]
    st.pop("closed_trades", None)
    return _r(st)


@tool("get_portfolio_performance", "Time-weighted and money-weighted returns, period returns, volatility, drawdown, benchmark comparison.",
      {"portfolio_id": {"type": "integer"}})
def t_perf(ctx, portfolio_id: int | None = None):
    h = pfs.portfolio_history(ctx.db, _pf(ctx, portfolio_id))
    return _r({"metrics": h["metrics"], "periods": h["periods"],
               "benchmark": {k: v for k, v in (h["benchmark"] or {}).items() if k != "values"} or None,
               "methodology": h["methodology"], "is_hypothetical": h["is_hypothetical"]})


@tool("get_portfolio_risk", "Independent risk-engine report: limit checks, concentration, correlations, VaR, exposures.",
      {"portfolio_id": {"type": "integer"}})
def t_risk(ctx, portfolio_id: int | None = None):
    rr = pfs.risk_report(ctx.db, _pf(ctx, portfolio_id), ctx.user.id)
    rr["checks"] = [c for c in rr["checks"] if c["status"] != "pass"][:20]
    rr.pop("correlation", None)
    return _r(rr)


@tool("stress_test_portfolio", "Apply a hypothetical stress scenario to the portfolio.",
      {"portfolio_id": {"type": "integer"}, "scenario": {"type": "string", "enum": list(STRESS_SCENARIOS)}}, ["scenario"])
def t_stress(ctx, scenario: str, portfolio_id: int | None = None):
    pf = _pf(ctx, portfolio_id)
    st = pfs.portfolio_state(ctx.db, pf)
    return _r(stress_test(st["positions"], pf.base_currency, scenario))


@tool("search_memory", "Search the agent's persistent research memory (past research notes, decisions, lessons, preferences).",
      {"query": {"type": "string"}, "symbol": {"type": "string"}, "limit": {"type": "integer"}}, [])
def t_search_memory(ctx, query: str = "", symbol: str | None = None, limit: int = 10):
    q = select(AgentMemory).where(AgentMemory.user_id == ctx.user.id)
    if symbol:
        s = md.find_security(ctx.db, symbol)
        if s:
            q = q.where(AgentMemory.security_id == s.id)
    if query:
        terms = [t for t in query.lower().split() if len(t) > 2][:6]
        if terms:
            q = q.where(or_(*[func.lower(AgentMemory.content).like(f"%{t}%") for t in terms]))
    rows = ctx.db.scalars(q.order_by(AgentMemory.created_at.desc()).limit(min(limit, 30))).all()
    return {"memories": [{"id": m.id, "kind": m.kind, "content": m.content, "tags": m.tags, "created_at": m.created_at.isoformat(),
                          "ref": f"{m.ref_type}#{m.ref_id}" if m.ref_type else None} for m in rows]}


@tool("save_memory", "Save a durable research note, decision rationale, lesson or user preference to memory.",
      {"kind": {"type": "string", "enum": ["research", "decision", "lesson", "preference", "fact"]},
       "content": {"type": "string"}, "symbol": {"type": "string"},
       "tags": {"type": "array", "items": {"type": "string"}}}, ["kind", "content"], permission="memory")
def t_save_memory(ctx, kind: str, content: str, symbol: str | None = None, tags: list[str] | None = None):
    s = md.find_security(ctx.db, symbol) if symbol else None
    m = AgentMemory(user_id=ctx.user.id, kind=kind, content=content[:4000], tags=(tags or [])[:10], security_id=s.id if s else None)
    ctx.db.add(m)
    ctx.db.flush()
    return {"saved": m.id}


@tool("list_reports", "List previous research reports (optionally for one symbol) with conclusions and dates.",
      {"symbol": {"type": "string"}, "limit": {"type": "integer"}})
def t_list_reports(ctx, symbol: str | None = None, limit: int = 10):
    q = select(ResearchReport).where(ResearchReport.user_id == ctx.user.id)
    if symbol:
        q = q.where(ResearchReport.security_id == _sec(ctx, symbol).id)
    rows = ctx.db.scalars(q.order_by(ResearchReport.created_at.desc()).limit(min(limit, 30))).all()
    return {"reports": [{"id": r.id, "title": r.title, "conclusion": r.conclusion, "created_at": r.created_at.isoformat(),
                         "data_quality": r.data_quality, "summary": r.sections.get("executive_summary")} for r in rows]}


@tool("get_report", "Fetch a stored research report by id (all sections, sources and calculations).",
      {"report_id": {"type": "integer"}}, ["report_id"])
def t_get_report(ctx, report_id: int):
    r = ctx.db.get(ResearchReport, report_id)
    if r is None or r.user_id != ctx.user.id:
        raise ToolError("report not found")
    sec = dict(r.sections)
    val = sec.get("valuation") or {}
    if val.get("available"):
        sec["valuation"] = {k: val.get(k) for k in ("price", "assumptions", "margin_of_safety", "limitations")} | {
            "dcf_value_per_share": (val.get("dcf") or {}).get("value_per_share"),
            "scenarios": {k: v["value_per_share"] for k, v in (val.get("scenarios") or {}).get("scenarios", {}).items()}}
    return _r({"id": r.id, "title": r.title, "conclusion": r.conclusion, "created_at": r.created_at.isoformat(),
               "sections": sec, "sources": r.sources[:12], "model_version": r.model_version})


@tool("get_prediction_record", "The prediction registry scorecard (hit rate, Brier score, calibration) and recent predictions.",
      {"symbol": {"type": "string"}})
def t_predictions(ctx, symbol: str | None = None):
    q = select(Prediction).where(Prediction.user_id == ctx.user.id)
    if symbol:
        q = q.where(Prediction.security_id == _sec(ctx, symbol).id)
    rows = ctx.db.scalars(q.order_by(Prediction.created_at.desc()).limit(15)).all()
    return _r({"scorecard": learning.prediction_scorecard(ctx.db, ctx.user.id),
               "recent": [{"id": p.id, "security_id": p.security_id, "kind": p.kind, "direction": p.direction,
                           "probability": p.probability, "start": p.start_date.isoformat(), "evaluate_on": p.evaluate_on.isoformat(),
                           "status": p.status, "outcome": p.outcome, "rationale": p.rationale[:200]} for p in rows]})


@tool("list_backtests", "Recent backtests and their metrics/warnings for a strategy.",
      {"strategy": {"type": "string", "enum": list(STRATEGIES)}})
def t_backtests(ctx, strategy: str | None = None):
    q = select(Backtest).where(Backtest.user_id == ctx.user.id)
    if strategy:
        q = q.where(Backtest.strategy_key == strategy)
    rows = ctx.db.scalars(q.order_by(Backtest.created_at.desc()).limit(5)).all()
    return _r({"backtests": [{"id": b.id, "strategy": b.strategy_key, "config": {k: b.config.get(k) for k in ("method", "start", "end", "version", "costs")},
                              "metrics": {k: v for k, v in b.metrics.items() if k != "sensitivity"}, "warnings": b.warnings,
                              "data_quality": b.data_quality} for b in rows]})


# ------------------------------------------------------------------ research (audited side effects)
@tool("generate_research_report",
      "Generate and store a standardized research report (thesis, evidence, valuation, risks, invalidation, sources). "
      "Material conclusions are automatically recorded in the prediction registry.",
      {"symbol": {"type": "string"}, "horizon": {"type": "string", "enum": ["long", "short"]},
       "strategy": {"type": "string", "enum": list(STRATEGIES)}, "portfolio_id": {"type": "integer"}}, ["symbol"],
      permission="research")
def t_gen_report(ctx, symbol: str, horizon: str = "long", strategy: str | None = None, portfolio_id: int | None = None):
    s = _sec(ctx, symbol)
    rep = research.generate_report(ctx.db, ctx.user.id, s, horizon, strategy, portfolio_id, created_by="agent")
    audit(ctx.db, ctx.user.id, "report.generate", "research_report", rep.id, {"symbol": s.symbol}, actor=ctx.actor)
    ctx.created.append({"type": "report", "id": rep.id})
    return {"report_id": rep.id, "title": rep.title, "conclusion": rep.conclusion,
            "summary": rep.sections.get("executive_summary"), "link": f"/research/reports/{rep.id}"}


@tool("register_prediction",
      "Record a forecast in the prediction registry BEFORE its window starts (baseline price captured now). probability = "
      "probability that the stated direction is correct (0.5-0.99).",
      {"symbol": {"type": "string"}, "direction": {"type": "string", "enum": ["up", "down"]},
       "probability": {"type": "number"}, "horizon_days": {"type": "integer"},
       "kind": {"type": "string", "enum": ["direction", "outperform"]}, "rationale": {"type": "string"}},
      ["symbol", "direction", "probability", "horizon_days", "rationale"], permission="research")
def t_register_prediction(ctx, symbol: str, direction: str, probability: float, horizon_days: int, rationale: str,
                          kind: str = "direction"):
    s = _sec(ctx, symbol)
    try:
        p = learning.create_prediction(ctx.db, ctx.user.id, s, direction, probability, horizon_days, kind, rationale,
                                       source="agent", model_version="agent-llm")
    except ValueError as e:
        raise ToolError(str(e)) from e
    audit(ctx.db, ctx.user.id, "prediction.create", "prediction", p.id, {"symbol": s.symbol}, actor=ctx.actor)
    return {"prediction_id": p.id, "evaluate_on": p.evaluate_on.isoformat(), "baseline_price": p.baseline_price,
            "criteria": p.evaluation_criteria}


@tool("create_alert", "Create a monitoring alert. Kinds: " + ", ".join(ALERT_KINDS),
      {"kind": {"type": "string", "enum": list(ALERT_KINDS)}, "symbol": {"type": "string"}, "portfolio_id": {"type": "integer"},
       "params": {"type": "object"}}, ["kind"], permission="research")
def t_create_alert(ctx, kind: str, symbol: str | None = None, portfolio_id: int | None = None, params: dict | None = None):
    s = _sec(ctx, symbol) if symbol else None
    if portfolio_id:
        _pf(ctx, portfolio_id)
    try:
        validate_alert(kind, params or {}, s.id if s else None, portfolio_id)
    except ValueError as e:
        raise ToolError(str(e)) from e
    a = Alert(user_id=ctx.user.id, kind=kind, security_id=s.id if s else None, portfolio_id=portfolio_id, params=params or {})
    ctx.db.add(a)
    ctx.db.flush()
    audit(ctx.db, ctx.user.id, "alert.create", "alert", a.id, {"kind": kind}, actor=ctx.actor)
    return {"alert_id": a.id}


@tool("queue_research_job",
      "Queue a background research job now (it runs in the worker): research_cycle, screening, watchlist_review, "
      "filings_monitor, prediction_evaluation, strategy_validation, data_refresh.",
      {"job_type": {"type": "string", "enum": ["research_cycle", "screening", "watchlist_review", "filings_monitor",
                                              "prediction_evaluation", "strategy_validation", "data_refresh"]},
       "params": {"type": "object"}}, ["job_type"], permission="research")
def t_queue_job(ctx, job_type: str, params: dict | None = None):
    from app.worker.queue import enqueue

    jr = enqueue(ctx.db, job_type, params or {}, user_id=ctx.user.id, trigger="agent")
    audit(ctx.db, ctx.user.id, "job.enqueue", "job_run", jr.id, {"job_type": job_type}, actor=ctx.actor)
    return {"job_run_id": jr.id, "status": jr.status}


# ------------------------------------------------------------------ proposals (require user approval)
@tool("propose_watchlist_change", "Propose adding/removing a security to/from a watchlist. Requires the user's approval.",
      {"symbol": {"type": "string"}, "action": {"type": "string", "enum": ["add", "remove"]},
       "watchlist_id": {"type": "integer"}, "rationale": {"type": "string"}}, ["symbol", "action", "rationale"],
      permission="propose")
def t_propose_watch(ctx, symbol: str, action: str, rationale: str, watchlist_id: int | None = None):
    s = _sec(ctx, symbol)
    wl = None
    if watchlist_id:
        wl = ctx.db.get(Watchlist, watchlist_id)
        if wl is None or wl.user_id != ctx.user.id:
            raise ToolError("watchlist not found")
    else:
        wl = ctx.db.scalars(select(Watchlist).where(Watchlist.user_id == ctx.user.id).order_by(Watchlist.id)).first()
        if wl is None:
            raise ToolError("user has no watchlist")
    pa = PendingAction(user_id=ctx.user.id, action_type="watchlist_" + action,
                       payload={"watchlist_id": wl.id, "watchlist": wl.name, "security_id": s.id, "symbol": s.symbol},
                       rationale=rationale[:2000])
    ctx.db.add(pa)
    ctx.db.flush()
    ctx.pending_actions.append(pa.id)
    audit(ctx.db, ctx.user.id, "proposal.create", "pending_action", pa.id, {"type": pa.action_type}, actor=ctx.actor)
    return {"pending_action_id": pa.id, "status": "awaiting user approval"}


@tool("propose_paper_trade",
      "Propose a buy/sell in a HYPOTHETICAL (paper/research) portfolio. The independent risk engine runs a pre-trade check; "
      "nothing executes until the user approves. Live trading is not available.",
      {"portfolio_id": {"type": "integer"}, "symbol": {"type": "string"}, "side": {"type": "string", "enum": ["buy", "sell"]},
       "quantity": {"type": "number"}, "rationale": {"type": "string"}}, ["portfolio_id", "symbol", "side", "quantity", "rationale"],
      permission="propose")
def t_propose_trade(ctx, portfolio_id: int, symbol: str, side: str, quantity: float, rationale: str):
    from app.services.trading import pre_trade

    pf = _pf(ctx, portfolio_id)
    if not pf.is_hypothetical or pf.kind == "live":
        raise ToolError("The agent may only propose trades in hypothetical portfolios.")
    s = _sec(ctx, symbol)
    chk = pre_trade(ctx.db, ctx.user.id, pf, s, side, quantity)
    pa = PendingAction(user_id=ctx.user.id, action_type="paper_trade",
                       payload={"portfolio_id": pf.id, "portfolio": pf.name, "security_id": s.id, "symbol": s.symbol,
                                "side": side, "quantity": quantity, "reference_price": chk["price"]},
                       rationale=rationale[:2000], risk_check=chk["check"])
    ctx.db.add(pa)
    ctx.db.flush()
    ctx.pending_actions.append(pa.id)
    audit(ctx.db, ctx.user.id, "proposal.create", "pending_action", pa.id, {"type": "paper_trade", "approved_by_risk": chk["check"]["approved"]},
          actor=ctx.actor)
    return {"pending_action_id": pa.id, "risk_check": chk["check"], "status": "awaiting user approval"}


def tool_definitions(exclude_permissions: set[str] | None = None) -> list[dict]:
    ex = exclude_permissions or set()
    return [{"name": t.name, "description": t.description, "input_schema": t.schema}
            for t in REGISTRY.values() if t.permission not in ex]


def execute_tool(ctx: AgentContext, name: str, args: dict, allowed_permissions: set[str] | None = None) -> dict:
    t = REGISTRY.get(name)
    if t is None:
        return {"ok": False, "error": f"unknown tool '{name}'"}
    if allowed_permissions is not None and t.permission not in allowed_permissions:
        return {"ok": False, "error": f"tool '{name}' is not permitted in this context"}
    if not isinstance(args, dict):
        return {"ok": False, "error": "tool input must be an object"}
    allowed = set(t.schema["properties"])
    unknown = set(args) - allowed
    if unknown:
        return {"ok": False, "error": f"unexpected arguments: {sorted(unknown)}"}
    missing = [r for r in t.schema.get("required", []) if r not in args]
    if missing:
        return {"ok": False, "error": f"missing required arguments: {missing}"}
    sp = ctx.db.begin_nested()
    try:
        res = t.handler(ctx, **args)
        sp.commit()
        return {"ok": True, "result": res}
    except ToolError as e:
        sp.rollback()
        return {"ok": False, "error": str(e)}
    except Exception as e:  # noqa: BLE001 - report tool failures to the model rather than crashing the turn
        sp.rollback()
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


def to_json(x: Any, limit: int = 24000) -> str:
    s = json.dumps(x, default=str)
    if len(s) > limit:
        s = s[:limit] + '..."[truncated]"'
    return s



