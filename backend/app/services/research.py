"""Company research: overview, auto-valuation with exposed assumptions, and standardized research reports."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.agent.safety import wrap_untrusted
from app.core.finance import ratios, risk_metrics, valuation
from app.core.screening import setups
from app.core.screening.engine import risk_flags
from app.core.screening.strategies import STRATEGIES
from app.models import (
    Backtest,
    CorporateEvent,
    Filing,
    NewsItem,
    Portfolio,
    PriceBar,
    Prediction,
    ResearchReport,
    Security,
)
from app.providers.sample import last_business_day
from app.services import market_data as md
from app.services.panel import FEATURES, load_panel
from app.services.regime import market_regime, pick_benchmark

MODEL_VERSION = "research-v1"


def ohlcv(db: Session, sec_id: int, days: int = 900) -> setups.OHLCV:
    rows = db.execute(select(PriceBar.date, PriceBar.open, PriceBar.high, PriceBar.low, PriceBar.close, PriceBar.volume)
                      .where(PriceBar.security_id == sec_id, PriceBar.date >= last_business_day() - timedelta(days=days))
                      .order_by(PriceBar.date)).all()
    c = [r[4] for r in rows]
    return setups.OHLCV([r[0] for r in rows], [r[1] or r[4] for r in rows], [r[2] or r[4] for r in rows],
                        [r[3] or r[4] for r in rows], c, [r[5] or 0.0 for r in rows])


def upcoming_events(db: Session, sec_id: int) -> list[dict]:
    today = datetime.now(timezone.utc).date()
    evs = db.scalars(select(CorporateEvent).where(CorporateEvent.security_id == sec_id, CorporateEvent.event_date >= today)
                     .order_by(CorporateEvent.event_date)).all()
    return [{"kind": e.kind, "date": e.event_date.isoformat(), "days_until": (e.event_date - today).days,
             "details": e.details, "source": e.source, "quality": e.quality} for e in evs]


def recent_news(db: Session, sec_id: int, limit: int = 10) -> list[dict]:
    rows = db.scalars(select(NewsItem).where(NewsItem.security_id == sec_id).order_by(NewsItem.published_at.desc()).limit(limit)).all()
    return [{"title": wrap_untrusted(n.title, n.source, 300), "url": n.url, "publisher": n.publisher,
             "published_at": n.published_at.isoformat(), "summary": wrap_untrusted(n.summary, n.source),
             "sentiment": n.sentiment, "source": n.source, "quality": n.quality} for n in rows]


def recent_filings(db: Session, sec_id: int, limit: int = 12) -> list[dict]:
    rows = db.scalars(select(Filing).where(Filing.security_id == sec_id).order_by(Filing.filed_date.desc()).limit(limit)).all()
    return [{"form": f.form, "filed_date": f.filed_date.isoformat(), "period": f.period.isoformat() if f.period else None,
             "accession": f.accession, "url": f.url, "description": f.description, "source": f.source, "quality": f.quality}
            for f in rows]


def security_overview(db: Session, sec: Security) -> dict:
    q = md.get_quote(db, sec)
    panel = load_panel(db, [sec.id])
    feats = panel.features(sec.id, last_business_day()) if sec.id in panel.securities else {}
    st = md.get_statements(db, sec.id)
    analysis = ratios.analyze(st["statements"]) if st["statements"] else None
    o = ohlcv(db, sec.id)
    evs = upcoming_events(db, sec.id)
    return {
        "security": md.security_dict(sec), "quote": q,
        "features": {k: {"value": v, **FEATURES[k]} for k, v in feats.items()},
        "fundamentals": {"analysis": analysis, "latest_filed": st["latest_filed"], "sources": st["sources"],
                         "qualities": st["qualities"], "available": bool(st["statements"])},
        "technicals": setups.technical_snapshot(o) if o.close else {},
        "setups": setups.detect(o, events=evs) if o.close else [],
        "events": evs, "filings": recent_filings(db, sec.id), "news": recent_news(db, sec.id),
        "risk_flags": risk_flags(feats, sec, evs),
    }


def estimate_beta(db: Session, sec: Security) -> float | None:
    bench = pick_benchmark(db)
    if bench is None or bench.id == sec.id:
        return None
    start = last_business_day() - timedelta(days=730)
    d1, c1 = md.close_series(db, sec.id, start)
    d2, c2 = md.close_series(db, bench.id, start)
    common = sorted(set(d1) & set(d2))
    if len(common) < 120:
        return None
    m1, m2 = dict(zip(d1, c1, strict=True)), dict(zip(d2, c2, strict=True))
    a = [m1[common[i]] / m1[common[i - 1]] - 1 for i in range(1, len(common))]
    b = [m2[common[i]] / m2[common[i - 1]] - 1 for i in range(1, len(common))]
    return risk_metrics.beta(a, b)


def auto_valuation(db: Session, sec: Security, overrides: dict | None = None) -> dict:
    """DCF + comparables with every assumption exposed. Returns ``available: False`` with reasons when inputs are
    missing rather than inventing numbers."""
    overrides = overrides or {}
    st = md.get_statements(db, sec.id)
    stm = st["statements"]
    reasons = []
    if not stm:
        return {"available": False, "reasons": ["No financial statements available for this security."]}
    ps = ratios.periods(stm)
    latest = ps[-1]
    fcfs = []
    for p in ps[-3:]:
        ocf, capex = stm.get("operating_cash_flow", {}).get(p), stm.get("capex", {}).get(p)
        if ocf is not None and capex is not None:
            fcfs.append(ocf - capex)
    shares = stm.get("shares_outstanding", {}).get(latest)
    debt = (stm.get("long_term_debt", {}).get(latest) or 0) + (stm.get("short_term_debt", {}).get(latest) or 0)
    cash = stm.get("cash", {}).get(latest)
    if not fcfs:
        reasons.append("Free cash flow history (operating cash flow and capex) unavailable.")
    if not shares:
        reasons.append("Diluted share count unavailable.")
    q = md.get_quote(db, sec)
    if q["price"] is None:
        reasons.append("No current price.")
    if reasons:
        return {"available": False, "reasons": reasons}
    assumptions = []
    hist_g = ratios.growth(stm, "revenue", 3)
    g0 = overrides.get("initial_growth", min(max(hist_g if hist_g is not None else 0.03, -0.05), 0.15))
    assumptions.append({"name": "Initial FCF growth", "value": g0,
                        "basis": f"3y revenue CAGR {hist_g:.1%} clamped to [−5%, 15%]" if hist_g is not None else "default 3% (no growth history)"})
    tg = overrides.get("terminal_growth", 0.025)
    assumptions.append({"name": "Terminal growth", "value": tg, "basis": "long-run nominal growth assumption"})
    rf_obs = md.macro_latest(db, "DGS10")
    if "discount_rate" in overrides:
        r = overrides["discount_rate"]
        wacc_detail = None
        assumptions.append({"name": "Discount rate", "value": r, "basis": "user override"})
    else:
        if rf_obs and rf_obs["quality"] != "sample":
            rf, rf_basis = rf_obs["value"] / 100, f"10y Treasury {rf_obs['value']:.2f}% ({rf_obs['source']} {rf_obs['date']})"
        elif rf_obs:
            rf, rf_basis = rf_obs["value"] / 100, f"SAMPLE 10y yield {rf_obs['value']:.2f}% (synthetic)"
        else:
            rf, rf_basis = 0.04, "assumed 4.0% (no rate data)"
        beta = estimate_beta(db, sec)
        b_used = min(max(beta, 0.6), 2.0) if beta is not None else 1.0
        erp = overrides.get("equity_risk_premium", 0.05)
        interest = stm.get("interest_expense", {}).get(latest)
        kd = (interest / debt) if (interest and debt) else rf + 0.02
        mcap = q["price"] * shares
        wacc_detail = valuation.wacc(rf, erp, b_used, min(max(kd, rf), 0.15), 0.21, mcap, debt)
        r = max(wacc_detail["wacc"], tg + 0.02)
        assumptions += [
            {"name": "Risk-free rate", "value": rf, "basis": rf_basis},
            {"name": "Beta", "value": b_used, "basis": f"2y daily regression vs benchmark ({beta:.2f}), clamped [0.6, 2.0]" if beta is not None else "default 1.0"},
            {"name": "Equity risk premium", "value": erp, "basis": "assumption"},
            {"name": "Discount rate (WACC)", "value": r, "basis": "WACC = E/V·(rf+β·ERP) + D/V·kd·(1−21%) (floored at g+2%)"},
        ]
    years = int(overrides.get("years", 5))
    path = [g0 + (tg - g0) * i / years for i in range(years)]
    base_fcf = float(np.mean(fcfs))
    assumptions.append({"name": "Base FCF", "value": base_fcf, "basis": f"average of last {len(fcfs)} fiscal years (OCF − capex)"})
    net_debt = debt - (cash or 0)
    kw = dict(base_fcf=base_fcf, growth_rates=path, discount_rate=r, terminal_growth=tg, net_debt=net_debt,
              shares_outstanding=shares)
    try:
        d = valuation.dcf(**kw)
    except valuation.ValuationError as e:
        return {"available": False, "reasons": [str(e)]}
    sens = valuation.dcf_sensitivity(kw, [round(r + x, 4) for x in (-0.02, -0.01, 0, 0.01, 0.02)],
                                     [round(tg + x, 4) for x in (-0.01, -0.005, 0, 0.005, 0.01)])
    scen = valuation.scenario_dcf(kw, {
        "bear": {"growth_rates": [g - 0.05 for g in path], "discount_rate": r + 0.01, "terminal_growth": max(tg - 0.01, 0.0), "probability": 0.25},
        "base": {"probability": 0.5},
        "bull": {"growth_rates": [g + 0.03 for g in path], "discount_rate": max(r - 0.005, tg + 0.015), "terminal_growth": tg + 0.005, "probability": 0.25},
    })
    mos = valuation.margin_of_safety(d["value_per_share"], q["price"]) if d["value_per_share"] > 0 else None
    comps = comparables(db, sec)
    return {
        "available": True, "currency": sec.currency, "price": q["price"], "price_as_of": q.get("as_of"),
        "price_quality": q["quality"], "statements_latest_period": latest, "statements_latest_filed": st["latest_filed"],
        "statement_sources": st["sources"], "assumptions": assumptions, "dcf": d, "wacc": wacc_detail,
        "sensitivity": sens, "scenarios": scen, "margin_of_safety": mos, "comparables": comps,
        "limitations": [
            "Intrinsic value estimates are highly sensitive to discount rate and terminal growth (see sensitivity table).",
            "Historical free cash flow may not represent future cash generation (cyclicality, investment phases).",
            "Units: statements and price must share a currency; ADRs and dual listings need manual checks.",
        ] + (["SAMPLE data: fictional company; values are illustrative only."] if sec.is_sample else []),
    }


def comparables(db: Session, sec: Security, n: int = 6) -> dict:
    if not sec.sector:
        return {"peers": [], "note": "No sector classification"}
    peers = db.scalars(select(Security).where(Security.sector == sec.sector, Security.asset_class == "equity",
                                              Security.is_sample.is_(sec.is_sample), Security.id != sec.id)).all()
    ids = [p.id for p in peers][:25] + [sec.id]
    panel = load_panel(db, ids)
    asof = last_business_day()
    rows = []
    for pid in ids:
        if pid not in panel.securities:
            continue
        f = panel.features(pid, asof)
        rows.append({"symbol": panel.securities[pid].symbol, "is_target": pid == sec.id,
                     **{k: f.get(k) for k in ("pe", "ev_ebitda", "ps", "fcf_yield", "roic_avg", "revenue_cagr_3y", "operating_margin")}})
    tgt = next((r for r in rows if r["is_target"]), None)
    others = [r for r in rows if not r["is_target"]][:n]
    st = md.get_statements(db, sec.id)["statements"]
    latest = ratios.periods(st)[-1] if st else None
    target_inputs = {}
    if latest:
        der = ratios.derived_for_period(st, latest)
        target_inputs = {"eps": st.get("eps_diluted", {}).get(latest), "revenue": st.get("revenue", {}).get(latest),
                         "shares": st.get("shares_outstanding", {}).get(latest), "ebitda": der.get("ebitda"),
                         "net_debt": der.get("net_debt")}
    rel = valuation.comparable_valuation(target_inputs, others) if (others and target_inputs) else None
    return {"peers": others, "target": tgt, "relative_valuation": rel,
            "note": "Peers = same sector in the database; peer quality depends on coverage."}


def calibration_confidence(db: Session, user_id: int) -> dict:
    preds = db.scalars(select(Prediction).where(Prediction.user_id == user_id, Prediction.status == "evaluated",
                                                Prediction.source.in_(["agent", "strategy"]))).all()
    n = len(preds)
    if n < 30:
        return {"calibrated": False, "n_evaluated": n,
                "explanation": f"Uncalibrated: only {n} evaluated system predictions (need ≥30 to measure calibration)."}
    hits = [1.0 if (p.outcome or {}).get("correct") else 0.0 for p in preds]
    stated = [p.probability for p in preds]
    return {"calibrated": True, "n_evaluated": n, "hit_rate": float(np.mean(hits)), "avg_stated": float(np.mean(stated)),
            "brier": float(np.mean([(s - h) ** 2 for s, h in zip(stated, hits, strict=True)]))}


def generate_report(db: Session, user_id: int, sec: Security, horizon: str = "long", strategy_key: str | None = None,
                    portfolio_id: int | None = None, created_by: str = "user", register_prediction: bool = True) -> ResearchReport:
    ov = security_overview(db, sec)
    val = auto_valuation(db, sec) if sec.asset_class == "equity" else {"available": False, "reasons": ["Not an operating company"]}
    regime = market_regime(db)
    feats = {k: v["value"] for k, v in ov["features"].items()}
    flags = ov["risk_flags"]
    severe = [f for f in flags if f["severity"] == "high"]
    q = ov["quote"]
    fa = ov["fundamentals"]["analysis"]
    limitations = []
    if q["price"] is None:
        limitations.append("No price data.")
    elif q["freshness"]["status"] != "fresh":
        limitations.append(f"Price is {q['freshness']['status']} ({q['freshness']['age_business_days']} business days old).")
    if not fa or fa["summary"]["n_periods"] < 3:
        limitations.append("Fewer than 3 years of financial statements.")
    if not val.get("available"):
        limitations += val.get("reasons", [])
    if sec.is_sample:
        limitations.append("SAMPLE data: fictional instrument. This report demonstrates the workflow only.")
    if not ov["news"]:
        limitations.append("No news coverage configured or available.")
    failed_checks = [c for c in (fa or {}).get("checks", []) if not c["ok"]]
    if failed_checks:
        limitations.append(f"{len(failed_checks)} statement consistency check(s) failed; see evidence.")

    mos = (val.get("margin_of_safety") or {}).get("margin_of_safety") if val.get("available") else None
    comps_v = (((val.get("comparables") or {}).get("relative_valuation") or {}).get("median_value_per_share")) if val.get("available") else None
    dcf_v = val["dcf"]["value_per_share"] if val.get("available") else None
    agreement = None
    if dcf_v and comps_v and dcf_v > 0 and comps_v > 0:
        agreement = abs(dcf_v / comps_v - 1)
    quality_ok = (feats.get("roic_avg") or 0) > 0.08 or (feats.get("fcf_consistency") or 0) >= 0.6
    insufficient = q["price"] is None or q["freshness"]["status"] == "missing" or (sec.asset_class == "equity" and not val.get("available"))
    if insufficient:
        conclusion = "insufficient_evidence"
    elif len(severe) >= 2:
        conclusion = "reject"
    elif mos is not None and mos > 0.15 and not severe and quality_ok:
        conclusion = "consider"
    else:
        conclusion = "monitor"
    strength = "low"
    if not insufficient:
        points = (1 if fa and fa["summary"]["n_periods"] >= 5 else 0) + (1 if agreement is not None and agreement < 0.25 else 0) \
            + (1 if q["freshness"]["status"] == "fresh" else 0) + (0 if failed_checks else 1)
        strength = "high" if points >= 4 else ("moderate" if points >= 2 else "low")
    calib = calibration_confidence(db, user_id)

    thesis = []
    if feats.get("roic_avg") is not None:
        thesis.append(f"Average ROIC of {feats['roic_avg']:.1%} over recent years" + (" indicates value-creating reinvestment." if feats["roic_avg"] > 0.12 else "."))
    if feats.get("revenue_cagr_3y") is not None:
        thesis.append(f"Revenue has compounded at {feats['revenue_cagr_3y']:.1%} per year over 3 years.")
    if mos is not None:
        thesis.append(f"Base-case DCF value {dcf_v:,.2f} {sec.currency} vs price {q['price']:,.2f} implies a margin of safety of {mos:.0%} "
                      "under the stated assumptions.")
    if feats.get("mom_12_1") is not None:
        thesis.append(f"12-1 month price momentum: {feats['mom_12_1']:.1%}.")
    counter = []
    if val.get("available"):
        bear = val["scenarios"]["scenarios"]["bear"]["value_per_share"]
        counter.append(f"Bear case value {bear:,.2f}: lower growth and a higher discount rate would {'erase' if bear < q['price'] else 'reduce'} the margin of safety.")
        tshare = val["dcf"].get("terminal_share_of_ev")
        if tshare and tshare > 0.65:
            counter.append(f"{tshare:.0%} of DCF value comes from the terminal period; the estimate depends heavily on long-run assumptions.")
    counter += [f["message"] for f in flags if f["severity"] in ("high", "medium")][:5]
    if feats.get("pe") and feats["pe"] > 30:
        counter.append("Valuation already discounts strong execution; disappointments could compress the multiple.")

    pf_fit = None
    if portfolio_id:
        from app.services.portfolio import limits_for, portfolio_state

        pf = db.get(Portfolio, portfolio_id)
        if pf and pf.user_id == user_id:
            stt = portfolio_state(db, pf)
            lim = limits_for(db, user_id, pf.id)
            existing = next((p for p in stt["positions"] if p["security_id"] == sec.id), None)
            sector_w = (stt["exposures"].get("sector") or {}).get(sec.sector or "Unclassified", 0.0)
            pf_fit = {"portfolio": pf.name, "already_held_weight": existing["weight"] if existing else 0.0,
                      "sector_weight_now": sector_w, "max_position_pct": lim.max_position_pct,
                      "max_sector_pct": lim.max_sector_pct,
                      "suggested_max_new_weight": max(0.0, min(lim.max_position_pct - (existing["weight"] if existing else 0.0),
                                                               lim.max_sector_pct - sector_w)),
                      "note": "Sizing ceiling from risk limits; not a recommendation to buy."}

    bt = None
    if strategy_key:
        last_bt = db.scalars(select(Backtest).where(Backtest.user_id == user_id, Backtest.strategy_key == strategy_key)
                             .order_by(Backtest.created_at.desc()).limit(1)).first()
        if last_bt:
            bt = {"backtest_id": last_bt.id, "created_at": last_bt.created_at.isoformat(), "metrics": last_bt.metrics,
                  "warnings": last_bt.warnings, "data_quality": last_bt.data_quality}
    horizon_txt = "3–5 years" if horizon == "long" else "2–6 weeks"
    invalidation = STRATEGIES.get(strategy_key or "", {}).get("invalidation", []) or [
        "Free cash flow declines materially versus the base-case path",
        "Leverage rises above 3x net debt/EBITDA",
        "Price exceeds base-case value (margin of safety exhausted)",
    ]
    if horizon == "short" and ov["setups"]:
        s0 = ov["setups"][0]
        invalidation = [s0["invalidation"]["rule"]] + invalidation[:1]

    sources = [{"type": "price", "source": q.get("source"), "as_of": q.get("as_of"), "quality": q.get("quality")}]
    for s in ov["fundamentals"]["sources"]:
        sources.append({"type": "financial statements", "source": s, "latest_filed": ov["fundamentals"]["latest_filed"],
                        "quality": ",".join(ov["fundamentals"]["qualities"])})
    for f in ov["filings"][:5]:
        sources.append({"type": "filing", "source": f["source"], "form": f["form"], "date": f["filed_date"], "url": f["url"]})
    for n in ov["news"][:5]:
        sources.append({"type": "news", "source": n["source"], "date": n["published_at"], "url": n["url"],
                        "title": n["title"]["text"], "untrusted": True})
    if regime.get("benchmark"):
        sources.append({"type": "market regime", "source": regime["benchmark"]["symbol"], "as_of": regime.get("as_of")})
    calcs = []
    if val.get("available"):
        calcs.append({"name": "DCF", "formula": val["dcf"]["formula"], "inputs": val["dcf"]["inputs"],
                      "output": {"value_per_share": dcf_v, "enterprise_value": val["dcf"]["enterprise_value"]}})
        if val.get("wacc"):
            calcs.append({"name": "WACC", "formula": val["wacc"]["formula"], "output": val["wacc"]})
        if val.get("margin_of_safety"):
            calcs.append({"name": "Margin of safety", "formula": val["margin_of_safety"]["formula"], "output": val["margin_of_safety"]})

    title_c = {"consider": "Worth deeper consideration", "monitor": "Monitor", "reject": "Reject for now",
               "insufficient_evidence": "No reliable conclusion yet"}[conclusion]
    summary = (f"{sec.name} ({sec.symbol}) — {title_c}. "
               + (f"Base-case value {dcf_v:,.2f} vs price {q['price']:,.2f} {sec.currency} (MoS {mos:.0%}). " if mos is not None else "")
               + (f"{len(severe)} high-severity risk flag(s). " if severe else "")
               + f"Evidence strength: {strength}. "
               + ("Data limitations prevent a reliable conclusion. " if conclusion == "insufficient_evidence" else "")
               + ("SAMPLE DATA — illustrative only." if sec.is_sample else ""))
    sections = {
        "executive_summary": summary,
        "identification": {**md.security_dict(sec), "price": q.get("price"), "price_as_of": q.get("as_of"),
                           "price_quality": q.get("quality"), "statements_latest_filed": ov["fundamentals"]["latest_filed"],
                           "report_generated_at": datetime.now(timezone.utc).isoformat()},
        "thesis": thesis or ["No thesis can be formed from the available evidence."],
        "evidence": {"fundamental": {k: feats.get(k) for k in ("revenue_cagr_3y", "eps_cagr_3y", "gross_margin", "operating_margin",
                                                                 "fcf_margin", "fcf_consistency", "roic_avg", "net_debt_to_ebitda",
                                                                 "interest_coverage", "share_count_cagr_3y", "sbc_to_revenue")},
                     "quantitative": {k: feats.get(k) for k in ("mom_12_1", "mom_6m", "vol_252d", "max_dd_1y", "adv_usd_20d",
                                                                 "rsi14", "dist_52w_high")},
                     "consistency_checks_failed": failed_checks[:10]},
        "valuation": val,
        "market_context": {"regime": regime.get("label"), "explanation": regime.get("explanation"),
                           "benchmark": regime.get("benchmark"), "caveat": regime.get("caveat")},
        "risks": flags, "counterarguments": counter,
        "portfolio_fit": pf_fit,
        "horizon": {"horizon": horizon, "expected_holding": horizon_txt, "invalidation": invalidation},
        "short_term_setups": ov["setups"] if horizon == "short" else [],
        "strategy_tests": bt or {"note": "No backtest on record for this strategy. Run one in the Research Lab."},
        "data_limitations": limitations,
        "confidence": {"evidence_strength": strength, "valuation_method_disagreement": agreement, **calib},
        "reconsider_if": invalidation + ["New filings materially change revenue, margins or leverage",
                                         "Price moves enough to change the margin of safety by more than 15 points"],
    }
    rep = ResearchReport(user_id=user_id, security_id=sec.id, horizon=horizon,
                         title=f"{sec.symbol}: {title_c}", conclusion=conclusion,
                         confidence=calib.get("hit_rate") if calib.get("calibrated") else None,
                         sections=_jsonable(sections), sources=_jsonable(sources), calculations=_jsonable(calcs),
                         data_quality="sample" if sec.is_sample else (q.get("quality") or "unknown"),
                         model_version=MODEL_VERSION, created_by=created_by)
    db.add(rep)
    db.flush()
    if register_prediction and conclusion in ("consider", "reject") and q["price"] is not None:
        register_report_prediction(db, user_id, sec, rep, conclusion, strength, horizon)
    return rep


def register_report_prediction(db: Session, user_id: int, sec: Security, rep: ResearchReport, conclusion: str,
                               strength: str, horizon: str) -> Prediction | None:
    bench = pick_benchmark(db)
    if bench is None or bench.is_sample != sec.is_sample:
        return None
    bq = md.get_quote(db, bench)
    q = md.get_quote(db, sec)
    days = 365 if horizon == "long" else 30
    prob = {"high": 0.60, "moderate": 0.55, "low": 0.52}[strength]
    start = last_business_day()
    p = Prediction(user_id=user_id, security_id=sec.id, benchmark_security_id=bench.id, kind="outperform",
                   direction="up" if conclusion == "consider" else "down", probability=prob, horizon_days=days,
                   start_date=start, evaluate_on=start + timedelta(days=days), baseline_price=q["price"],
                   baseline_benchmark=bq["price"],
                   rationale=f"Report #{rep.id} conclusion '{conclusion}' (evidence {strength}).",
                   assumptions={"report_id": rep.id, "mapping": "consider→outperform, reject→underperform; "
                                "probability from evidence strength (v1 mapping, to be calibrated)"},
                   data_snapshot={"price": q["price"], "price_as_of": q.get("as_of"), "benchmark": bench.symbol,
                                  "benchmark_price": bq["price"]},
                   evaluation_criteria=f"Correct if total price return of {sec.symbol} {'exceeds' if conclusion == 'consider' else 'trails'} "
                                       f"{bench.symbol} over {days} calendar days (price return, dividends excluded).",
                   model_version=MODEL_VERSION, source="agent" if rep.created_by == "agent" else "strategy",
                   data_quality="sample" if sec.is_sample else "eod")
    db.add(p)
    db.flush()
    return p


def _jsonable(x):
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    if isinstance(x, (np.floating,)):
        return float(x)
    if isinstance(x, (np.integer,)):
        return int(x)
    if isinstance(x, (date, datetime)):
        return x.isoformat()
    if isinstance(x, float) and (x != x or x in (float("inf"), float("-inf"))):
        return None
    return x
