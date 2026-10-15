"""Valuation models with fully exposed inputs, formulas and assumptions.

Every function returns a dict containing ``inputs``, ``formula`` and intermediate values so the UI and the
research reports can show exactly how a number was produced. Results are estimates under stated
assumptions, never objective truths.
"""
from __future__ import annotations

import statistics
from collections.abc import Sequence


class ValuationError(ValueError):
    pass


def dcf(
    base_fcf: float,
    growth_rates: Sequence[float],
    discount_rate: float,
    terminal_growth: float,
    net_debt: float,
    shares_outstanding: float,
    mid_year: bool = False,
) -> dict:
    """Two-stage free-cash-flow-to-firm DCF.

    EV = sum_t FCF_t / (1+r)^t + TV / (1+r)^N, TV = FCF_N * (1+g) / (r - g)
    Equity = EV - net debt; value per share = Equity / shares.
    """
    if discount_rate <= terminal_growth:
        raise ValuationError("discount rate must exceed terminal growth")
    if shares_outstanding <= 0:
        raise ValuationError("shares outstanding must be positive")
    if not growth_rates:
        raise ValuationError("at least one explicit-period growth rate required")
    fcfs, pvs = [], []
    fcf = base_fcf
    for t, g in enumerate(growth_rates, start=1):
        fcf = fcf * (1 + g)
        exp = t - 0.5 if mid_year else t
        fcfs.append(fcf)
        pvs.append(fcf / (1 + discount_rate) ** exp)
    n = len(growth_rates)
    tv = fcfs[-1] * (1 + terminal_growth) / (discount_rate - terminal_growth)
    pv_tv = tv / (1 + discount_rate) ** n
    ev = sum(pvs) + pv_tv
    equity = ev - net_debt
    per_share = equity / shares_outstanding
    return {
        "model": "dcf_fcff_two_stage",
        "formula": "EV = Σ FCF_t/(1+r)^t + [FCF_N·(1+g)/(r−g)]/(1+r)^N ; Equity = EV − NetDebt ; Value/share = Equity/Shares",
        "inputs": {
            "base_fcf": base_fcf,
            "growth_rates": list(growth_rates),
            "discount_rate": discount_rate,
            "terminal_growth": terminal_growth,
            "net_debt": net_debt,
            "shares_outstanding": shares_outstanding,
            "mid_year": mid_year,
        },
        "projected_fcf": fcfs,
        "pv_fcf": pvs,
        "terminal_value": tv,
        "pv_terminal_value": pv_tv,
        "terminal_share_of_ev": pv_tv / ev if ev else None,
        "enterprise_value": ev,
        "equity_value": equity,
        "value_per_share": per_share,
        "warnings": _dcf_warnings(base_fcf, pv_tv, ev, equity),
    }


def _dcf_warnings(base_fcf: float, pv_tv: float, ev: float, equity: float) -> list[str]:
    w = []
    if base_fcf <= 0:
        w.append("Base free cash flow is not positive; a DCF on negative FCF is highly assumption-driven.")
    if ev and pv_tv / ev > 0.75:
        w.append("Terminal value exceeds 75% of enterprise value; result is dominated by long-run assumptions.")
    if equity <= 0:
        w.append("Net debt exceeds enterprise value; implied equity value is non-positive.")
    return w


def dcf_sensitivity(
    base_kwargs: dict, discount_rates: Sequence[float], terminal_growths: Sequence[float]
) -> dict:
    grid = []
    for r in discount_rates:
        row = []
        for g in terminal_growths:
            try:
                row.append(dcf(**{**base_kwargs, "discount_rate": r, "terminal_growth": g})["value_per_share"])
            except ValuationError:
                row.append(None)
        grid.append(row)
    return {"discount_rates": list(discount_rates), "terminal_growths": list(terminal_growths), "values": grid}


def scenario_dcf(base_kwargs: dict, scenarios: dict[str, dict]) -> dict:
    """Run named scenarios (bear/base/bull), each overriding DCF inputs; optional 'probability' per scenario."""
    out = {}
    weighted = 0.0
    total_p = 0.0
    for name, overrides in scenarios.items():
        p = overrides.get("probability")
        kw = {**base_kwargs, **{k: v for k, v in overrides.items() if k != "probability"}}
        res = dcf(**kw)
        out[name] = {"value_per_share": res["value_per_share"], "inputs": res["inputs"], "probability": p}
        if p is not None:
            weighted += p * res["value_per_share"]
            total_p += p
    return {
        "scenarios": out,
        "probability_weighted_value": weighted / total_p if total_p else None,
        "note": "Scenario probabilities are subjective assumptions, not measured frequencies.",
    }


def margin_of_safety(intrinsic_value: float, price: float) -> dict:
    if price <= 0:
        raise ValuationError("price must be positive")
    mos = (intrinsic_value - price) / intrinsic_value if intrinsic_value > 0 else None
    return {
        "formula": "MoS = (Intrinsic − Price) / Intrinsic",
        "intrinsic_value": intrinsic_value,
        "price": price,
        "margin_of_safety": mos,
        "upside": intrinsic_value / price - 1,
    }


def comparable_valuation(target: dict, peers: list[dict], multiples: Sequence[str] = ("pe", "ev_ebitda", "ps")) -> dict:
    """Relative valuation using peer median multiples.

    target: {"eps", "ebitda", "revenue", "net_debt", "shares"}; peers: [{"symbol", "pe", "ev_ebitda", "ps"}].
    """
    res = {"method": "peer median multiples", "peers": [p.get("symbol") for p in peers], "estimates": {}}
    for m in multiples:
        vals = [p[m] for p in peers if p.get(m) is not None and p[m] > 0]
        if len(vals) < 2:
            res["estimates"][m] = {"value_per_share": None, "reason": "fewer than 2 peers with positive multiple"}
            continue
        med = statistics.median(vals)
        vps = None
        if m == "pe" and target.get("eps") and target["eps"] > 0:
            vps = med * target["eps"]
        elif m == "ev_ebitda" and target.get("ebitda") and target["ebitda"] > 0 and target.get("shares"):
            vps = (med * target["ebitda"] - (target.get("net_debt") or 0)) / target["shares"]
        elif m == "ps" and target.get("revenue") and target.get("shares"):
            vps = med * target["revenue"] / target["shares"]
        res["estimates"][m] = {
            "peer_median": med,
            "peer_values": vals,
            "value_per_share": vps,
            "reason": None if vps is not None else "target metric missing or non-positive",
        }
    valid = [e["value_per_share"] for e in res["estimates"].values() if e.get("value_per_share")]
    res["median_value_per_share"] = statistics.median(valid) if valid else None
    return res


def wacc(
    risk_free: float, equity_risk_premium: float, beta: float, cost_of_debt_pre_tax: float, tax_rate: float,
    equity_value: float, debt_value: float,
) -> dict:
    ke = risk_free + beta * equity_risk_premium
    kd = cost_of_debt_pre_tax * (1 - tax_rate)
    v = equity_value + debt_value
    if v <= 0:
        raise ValuationError("capital structure must be positive")
    w = ke * equity_value / v + kd * debt_value / v
    return {
        "formula": "WACC = E/V·(rf + β·ERP) + D/V·kd·(1−t)",
        "cost_of_equity": ke,
        "after_tax_cost_of_debt": kd,
        "weight_equity": equity_value / v,
        "weight_debt": debt_value / v,
        "wacc": w,
    }
