"""Screening engine: evaluate criteria, rank by cross-sectional z-scores, and explain every result."""
from __future__ import annotations

import math
import statistics
from typing import Any

from app.core.screening.strategies import OPS

SEVERE_FLAGS = {"negative_equity", "fcf_negative_persistent", "low_interest_coverage"}


def evaluate_criteria(features: dict, criteria: list[dict]) -> dict:
    passed, failed, missing = [], [], []
    for c in criteria:
        v = features.get(c["feature"])
        row = {**c, "actual": v}
        if v is None:
            missing.append(row)
        elif OPS[c["op"]](v, c["value"]):
            passed.append(row)
        else:
            failed.append(row)
    req_failed = [c for c in failed if c.get("required")]
    req_missing = [c for c in missing if c.get("required")]
    n = len(criteria) or 1
    return {"passed": passed, "failed": failed, "missing": missing, "pass_ratio": len(passed) / n,
            "required_ok": not req_failed and not req_missing, "required_missing": bool(req_missing)}


def universe_ok(meta, features: dict, universe: dict) -> tuple[bool, str | None]:
    if universe.get("asset_classes") and meta.asset_class not in universe["asset_classes"]:
        return False, f"asset class {meta.asset_class} outside strategy universe"
    adv = features.get("adv_usd_20d")
    if universe.get("min_adv_usd") and (adv is None or adv < universe["min_adv_usd"]):
        return False, "insufficient liquidity (20d average dollar volume below minimum)" if adv is not None else "liquidity unknown"
    px = features.get("price")
    if universe.get("min_price") and (px is None or px < universe["min_price"]):
        return False, "price below minimum" if px is not None else "no price"
    return True, None


def zscores(values: dict[Any, float | None]) -> dict[Any, float]:
    xs = [v for v in values.values() if v is not None and math.isfinite(v)]
    if len(xs) < 2:
        return {k: 0.0 for k in values}
    m = statistics.mean(xs)
    sd = statistics.pstdev(xs) or 1.0
    return {k: (max(min((v - m) / sd, 3.0), -3.0) if v is not None else 0.0) for k, v in values.items()}


def risk_flags(features: dict, meta=None, events: list[dict] | None = None) -> list[dict]:
    f = features
    flags = []

    def add(code, sev, msg):
        flags.append({"code": code, "severity": sev, "message": msg})

    if f.get("net_debt_to_ebitda") is not None and f["net_debt_to_ebitda"] > 3.5:
        add("high_leverage", "high", f"Net debt/EBITDA {f['net_debt_to_ebitda']:.1f}x is elevated")
    if f.get("interest_coverage") is not None and f["interest_coverage"] < 2.5:
        add("low_interest_coverage", "high", f"Interest coverage {f['interest_coverage']:.1f}x leaves little cushion")
    if f.get("fcf_consistency") is not None and f["fcf_consistency"] < 0.5:
        add("fcf_negative_persistent", "high", "Free cash flow negative in most reported years")
    if f.get("share_count_cagr_3y") is not None and f["share_count_cagr_3y"] > 0.04:
        add("dilution", "medium", f"Share count growing {f['share_count_cagr_3y']:.1%}/yr (dilution)")
    if f.get("sbc_to_revenue") is not None and f["sbc_to_revenue"] > 0.10:
        add("high_sbc", "medium", f"Share-based compensation is {f['sbc_to_revenue']:.0%} of revenue")
    if f.get("accruals_ratio") is not None and abs(f["accruals_ratio"]) > 0.10:
        add("earnings_quality", "medium", "Large gap between net income and operating cash flow (accruals)")
    if f.get("vol_252d") is not None and f["vol_252d"] > 0.50:
        add("high_volatility", "medium", f"Annualized volatility {f['vol_252d']:.0%}")
    if f.get("adv_usd_20d") is not None and f["adv_usd_20d"] < 2_000_000:
        add("low_liquidity", "medium", "Thin trading; transaction costs and slippage may be significant")
    if f.get("revenue_cagr_3y") is not None and f["revenue_cagr_3y"] < -0.03:
        add("shrinking_revenue", "medium", f"Revenue shrinking {f['revenue_cagr_3y']:.1%}/yr")
    if f.get("pe") is not None and f["pe"] > 40:
        add("rich_valuation", "medium", f"P/E of {f['pe']:.0f} embeds demanding expectations")
    if f.get("fundamentals_age_days") is not None and f["fundamentals_age_days"] > 450:
        add("stale_fundamentals", "medium", "Latest annual statements are more than ~15 months old")
    if f.get("price_age_days") is not None and f["price_age_days"] > 7:
        add("stale_price", "high", "Latest price is more than a week old")
    for e in events or []:
        if e.get("kind") == "earnings" and e.get("days_until") is not None and 0 <= e["days_until"] <= 10:
            add("earnings_soon", "medium", f"Earnings expected in {e['days_until']} days (event risk)")
    if meta is not None and getattr(meta, "is_sample", False):
        add("sample_data", "info", "SAMPLE data: fictional instrument, results are illustrative only")
    return flags


def run_screen(panel, as_of, params: dict, security_ids: list[int] | None = None, events: dict | None = None) -> dict:
    """Returns ranked candidates (all required criteria met and pass ratio >= threshold) plus near-misses."""
    criteria = params.get("criteria", [])
    universe = params.get("universe", {})
    weights = params.get("weights", {})
    min_ratio = float(params.get("min_pass_ratio", 0.75))
    ids = security_ids or list(panel.securities)
    evaluated, excluded = {}, []
    feats = {}
    for sid in ids:
        meta = panel.securities[sid]
        f = panel.features(sid, as_of)
        feats[sid] = f
        ok, why = universe_ok(meta, f, universe)
        if not ok:
            excluded.append({"security_id": sid, "symbol": meta.symbol, "reason": why})
            continue
        evaluated[sid] = evaluate_criteria(f, criteria)
    eligible = [sid for sid, ev in evaluated.items() if ev["required_ok"] and ev["pass_ratio"] >= min_ratio - 1e-9]
    comp = {sid: 0.0 for sid in eligible}
    contributions: dict[int, dict] = {sid: {} for sid in eligible}
    for feat, w in weights.items():
        zs = zscores({sid: feats[sid].get(feat) for sid in eligible})
        for sid in eligible:
            comp[sid] += w * zs[sid]
            contributions[sid][feat] = round(w * zs[sid], 3)
    wsum = sum(abs(w) for w in weights.values()) or 1.0
    ranked = sorted(eligible, key=lambda s: comp[s], reverse=True)
    results = []
    for rank, sid in enumerate(ranked, start=1):
        meta = panel.securities[sid]
        results.append(_explain(sid, meta, rank, comp[sid] / wsum, contributions[sid], evaluated[sid], feats[sid], params,
                                (events or {}).get(sid)))
    near = []
    for sid, ev in evaluated.items():
        if sid in eligible:
            continue
        meta = panel.securities[sid]
        near.append({"security_id": sid, "symbol": meta.symbol, "name": meta.name, "pass_ratio": ev["pass_ratio"],
                     "failed": [_crit_str(c) for c in ev["failed"]], "missing": [c["feature"] for c in ev["missing"]],
                     "is_sample": meta.is_sample})
    near.sort(key=lambda r: -r["pass_ratio"])
    return {"as_of": as_of.isoformat(), "candidates": results, "near_misses": near[:15], "excluded": excluded,
            "n_universe": len(ids), "n_evaluated": len(evaluated), "n_candidates": len(results)}


def _crit_str(c: dict) -> str:
    a = c.get("actual")
    a_s = "n/a" if a is None else (f"{a:.3g}")
    return f"{c['feature']} {c['op']} {c['value']} (actual {a_s})"


def _explain(sid, meta, rank, score, contrib, ev, f, params, events) -> dict:
    flags = risk_flags(f, meta, events)
    missing_info = [c["feature"] for c in ev["missing"]]
    for k in ("pe", "fcf_yield", "roic_avg", "net_debt_to_ebitda", "revenue_cagr_3y", "price"):
        if f.get(k) is None and k not in missing_info:
            missing_info.append(k)
    if f.get("fundamentals_age_days") and f["fundamentals_age_days"] > 450:
        missing_info.append("recent fundamentals (latest annual report is old)")
    top = sorted(contrib.items(), key=lambda kv: -kv[1])[:3]
    why = (f"Passed {len(ev['passed'])}/{len(params.get('criteria', []))} criteria of '{params.get('title', '')}'; "
           f"strongest ranking contributors: " + ", ".join(f"{k} ({v:+.2f})" for k, v in top))
    further = ["Read the latest annual and quarterly filings (MD&A, risk factors, segment data)",
               "Verify the thesis drivers against management guidance and industry data",
               "Check valuation sensitivity to discount-rate and growth assumptions"]
    if any(fl["code"] == "earnings_soon" for fl in flags):
        further.append("Review expectations ahead of the upcoming earnings release")
    if any(fl["code"] in ("high_leverage", "low_interest_coverage") for fl in flags):
        further.append("Review debt maturity schedule and covenants in the filings")
    return {
        "security_id": sid, "symbol": meta.symbol, "name": meta.name, "sector": meta.sector, "country": meta.country,
        "is_sample": meta.is_sample, "rank": rank, "score": round(score, 4), "score_contributions": contrib,
        "why_selected": why,
        "criteria_passed": [_crit_str(c) for c in ev["passed"]],
        "criteria_failed": [_crit_str(c) for c in ev["failed"]],
        "criteria_missing": [c["feature"] for c in ev["missing"]],
        "key_metrics": {k: f.get(k) for k in ("price", "market_cap_usd", "pe", "ev_ebitda", "fcf_yield", "dividend_yield",
                                               "roic_avg", "operating_margin", "revenue_cagr_3y", "net_debt_to_ebitda",
                                               "mom_12_1", "vol_252d")},
        "risks": flags,
        "invalidation": params.get("invalidation", []),
        "missing_information": missing_info,
        "further_research": further,
        "disclaimer": "A screen ranking identifies what to research first; it is not a forecast or a recommendation.",
    }
