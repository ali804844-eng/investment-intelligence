"""Strategy definitions (versioned parameters live in the DB; these are the v1 defaults).

A strategy = universe filters + criteria (feature, operator, threshold, required) + ranking weights.
Ranking uses cross-sectional z-scores among candidates. A high score means "deserves research first",
not "will outperform".
"""
from __future__ import annotations

import copy

UNIVERSE_DEFAULT = {"asset_classes": ["equity"], "min_adv_usd": 1_000_000, "min_price": 1.0}

STRATEGIES: dict[str, dict] = {
    "quality": {
        "title": "Quality compounders",
        "horizon": "long",
        "description": "High and stable returns on capital, consistent free cash flow, conservative balance sheet.",
        "criteria": [
            {"feature": "roic_avg", "op": ">=", "value": 0.12, "required": True},
            {"feature": "fcf_consistency", "op": ">=", "value": 0.8, "required": False},
            {"feature": "operating_margin", "op": ">=", "value": 0.12, "required": False},
            {"feature": "net_debt_to_ebitda", "op": "<=", "value": 2.5, "required": False},
            {"feature": "share_count_cagr_3y", "op": "<=", "value": 0.02, "required": False},
        ],
        "weights": {"roic_avg": 1.0, "fcf_margin": 0.7, "operating_margin_stability": -0.5, "fcf_yield": 0.6},
        "min_pass_ratio": 0.75,
        "top_n": 8,
        "invalidation": ["ROIC falls below 10% for two consecutive years",
                         "Free cash flow turns negative or FCF conversion drops below 60%",
                         "Net debt/EBITDA rises above 3x (e.g. debt-funded acquisitions)"],
    },
    "value": {
        "title": "Value with balance-sheet support",
        "horizon": "long",
        "description": "Low valuation multiples with positive cash generation and manageable leverage.",
        "criteria": [
            {"feature": "pe", "op": "<=", "value": 15, "required": False},
            {"feature": "fcf_yield", "op": ">=", "value": 0.05, "required": True},
            {"feature": "net_debt_to_ebitda", "op": "<=", "value": 3.0, "required": False},
            {"feature": "interest_coverage", "op": ">=", "value": 4.0, "required": False},
            {"feature": "revenue_cagr_3y", "op": ">=", "value": -0.02, "required": False},
        ],
        "weights": {"fcf_yield": 1.0, "earnings_yield": 0.7, "ev_ebitda": -0.5, "roic_avg": 0.3},
        "min_pass_ratio": 0.75,
        "top_n": 8,
        "invalidation": ["FCF yield compresses below 3% without price appreciation (deteriorating cash flow)",
                         "Revenue declines more than 5% per year (possible value trap)",
                         "Interest coverage falls below 3x"],
    },
    "growth": {
        "title": "Growth with improving economics",
        "horizon": "long",
        "description": "Fast revenue growth with healthy gross margins and limited dilution.",
        "criteria": [
            {"feature": "revenue_cagr_3y", "op": ">=", "value": 0.15, "required": True},
            {"feature": "gross_margin", "op": ">=", "value": 0.40, "required": False},
            {"feature": "share_count_cagr_3y", "op": "<=", "value": 0.05, "required": False},
            {"feature": "fcf_margin", "op": ">=", "value": 0.0, "required": False},
        ],
        "weights": {"revenue_cagr_3y": 1.0, "fcf_margin": 0.6, "gross_margin": 0.4, "sbc_to_revenue": -0.4},
        "min_pass_ratio": 0.66,
        "top_n": 8,
        "invalidation": ["Revenue growth decelerates below 10%", "Gross margin declines more than 5 points",
                         "Dilution exceeds 5% per year"],
    },
    "momentum": {
        "title": "Price momentum (trend)",
        "horizon": "long",
        "description": "Strong 12-1 month relative strength in an established uptrend.",
        "criteria": [
            {"feature": "mom_12_1", "op": ">=", "value": 0.10, "required": True},
            {"feature": "above_sma200", "op": ">=", "value": 1, "required": False},
            {"feature": "vol_252d", "op": "<=", "value": 0.60, "required": False},
        ],
        "weights": {"mom_12_1": 1.0, "mom_6m": 0.5, "vol_252d": -0.4},
        "min_pass_ratio": 0.66,
        "top_n": 8,
        "invalidation": ["Price closes below the 200-day moving average", "12-1 momentum turns negative"],
    },
    "income": {
        "title": "Sustainable income",
        "horizon": "long",
        "description": "Meaningful dividend yield covered by free cash flow, with adequate interest coverage.",
        "criteria": [
            {"feature": "dividend_yield", "op": ">=", "value": 0.025, "required": True},
            {"feature": "fcf_payout_ratio", "op": "<=", "value": 0.80, "required": False},
            {"feature": "interest_coverage", "op": ">=", "value": 3.0, "required": False},
            {"feature": "payout_ratio", "op": "<=", "value": 0.85, "required": False},
        ],
        "weights": {"dividend_yield": 1.0, "fcf_payout_ratio": -0.6, "interest_coverage": 0.3},
        "min_pass_ratio": 0.66,
        "top_n": 8,
        "invalidation": ["Dividend not covered by FCF for two years", "Dividend cut announced",
                         "Interest coverage below 2.5x"],
    },
    "defensive": {
        "title": "Defensive / low volatility",
        "horizon": "long",
        "description": "Low price volatility, contained drawdowns and profitable operations.",
        "criteria": [
            {"feature": "vol_252d", "op": "<=", "value": 0.25, "required": True},
            {"feature": "max_dd_1y", "op": ">=", "value": -0.25, "required": False},
            {"feature": "net_debt_to_ebitda", "op": "<=", "value": 3.5, "required": False},
            {"feature": "operating_margin", "op": ">=", "value": 0.08, "required": False},
        ],
        "weights": {"vol_252d": -1.0, "roic_avg": 0.5, "max_dd_1y": 0.4},
        "min_pass_ratio": 0.66,
        "top_n": 8,
        "invalidation": ["Realized volatility rises above 35%", "Drawdown exceeds 25%"],
    },
}

OPS = {">=": lambda a, b: a >= b, "<=": lambda a, b: a <= b, ">": lambda a, b: a > b, "<": lambda a, b: a < b,
       "==": lambda a, b: a == b}


def default_params(key: str) -> dict:
    s = copy.deepcopy(STRATEGIES[key])
    s["universe"] = copy.deepcopy(UNIVERSE_DEFAULT)
    return s


def validate_params(p: dict) -> None:
    from app.services.panel import FEATURES

    for c in p.get("criteria", []):
        if c.get("feature") not in FEATURES:
            raise ValueError(f"unknown feature '{c.get('feature')}'")
        if c.get("op") not in OPS:
            raise ValueError(f"unknown operator '{c.get('op')}'")
        if not isinstance(c.get("value"), (int, float)):
            raise ValueError("criterion value must be numeric")
    for f in (p.get("weights") or {}):
        if f not in FEATURES:
            raise ValueError(f"unknown weight feature '{f}'")
    if not 0 < float(p.get("min_pass_ratio", 0.75)) <= 1:
        raise ValueError("min_pass_ratio must be in (0, 1]")
    if not 1 <= int(p.get("top_n", 8)) <= 100:
        raise ValueError("top_n must be between 1 and 100")
