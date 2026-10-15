"""Financial statement analysis: ratios, growth, quality and consistency checks, and a safe formula evaluator.

Input format: ``{metric: {period_end_iso: value}}`` for annual (FY) data. Canonical metric names are listed in
``METRICS``. Missing inputs produce ``None`` with a reason, never an invented value.
"""
from __future__ import annotations

import ast
import math
import operator
from collections.abc import Mapping

METRICS: dict[str, dict] = {
    # income statement
    "revenue": {"label": "Revenue", "statement": "income"},
    "cost_of_revenue": {"label": "Cost of revenue", "statement": "income"},
    "gross_profit": {"label": "Gross profit", "statement": "income"},
    "operating_income": {"label": "Operating income", "statement": "income"},
    "interest_expense": {"label": "Interest expense", "statement": "income"},
    "pretax_income": {"label": "Pre-tax income", "statement": "income"},
    "income_tax": {"label": "Income tax", "statement": "income"},
    "net_income": {"label": "Net income", "statement": "income"},
    "eps_diluted": {"label": "Diluted EPS", "statement": "income", "unit": "per_share"},
    "d_and_a": {"label": "Depreciation & amortization", "statement": "cashflow"},
    "sbc": {"label": "Share-based compensation", "statement": "cashflow"},
    # balance sheet
    "cash": {"label": "Cash & equivalents", "statement": "balance"},
    "receivables": {"label": "Receivables", "statement": "balance"},
    "inventory": {"label": "Inventory", "statement": "balance"},
    "current_assets": {"label": "Current assets", "statement": "balance"},
    "total_assets": {"label": "Total assets", "statement": "balance"},
    "current_liabilities": {"label": "Current liabilities", "statement": "balance"},
    "short_term_debt": {"label": "Short-term debt", "statement": "balance"},
    "long_term_debt": {"label": "Long-term debt", "statement": "balance"},
    "total_liabilities": {"label": "Total liabilities", "statement": "balance"},
    "total_equity": {"label": "Shareholders' equity", "statement": "balance"},
    "shares_outstanding": {"label": "Diluted shares", "statement": "balance", "unit": "shares"},
    # cash flow
    "operating_cash_flow": {"label": "Operating cash flow", "statement": "cashflow"},
    "capex": {"label": "Capital expenditure (spend, positive)", "statement": "cashflow"},
    "dividends_paid": {"label": "Dividends paid (positive)", "statement": "cashflow"},
    "buybacks": {"label": "Share repurchases (positive)", "statement": "cashflow"},
}

Series = Mapping[str, Mapping[str, float]]


def _div(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0 or not math.isfinite(a) or not math.isfinite(b):
        return None
    return a / b


def periods(stmts: Series) -> list[str]:
    ps: set[str] = set()
    for vals in stmts.values():
        ps.update(vals.keys())
    return sorted(ps)


def _get(stmts: Series, metric: str, p: str) -> float | None:
    return stmts.get(metric, {}).get(p)


def derived_for_period(stmts: Series, p: str, prev: str | None = None) -> dict[str, float | None]:
    g = lambda m: _get(stmts, m, p)  # noqa: E731
    revenue = g("revenue")
    op = g("operating_income")
    ni = g("net_income")
    ocf = g("operating_cash_flow")
    capex = g("capex")
    da = g("d_and_a")
    debt = None
    if g("long_term_debt") is not None or g("short_term_debt") is not None:
        debt = (g("long_term_debt") or 0) + (g("short_term_debt") or 0)
    equity = g("total_equity")
    cash = g("cash")
    fcf = (ocf - capex) if (ocf is not None and capex is not None) else None
    ebitda = (op + da) if (op is not None and da is not None) else None
    pretax = g("pretax_income")
    tax = g("income_tax")
    tax_rate = _div(tax, pretax) if (pretax and pretax > 0) else None
    eff_tax = min(max(tax_rate, 0.0), 0.5) if tax_rate is not None else 0.21
    nopat = op * (1 - eff_tax) if op is not None else None
    invested = (equity + debt - (cash or 0)) if (equity is not None and debt is not None) else None
    if prev:
        pe_, pd_, pc_ = _get(stmts, "total_equity", prev), _get(stmts, "long_term_debt", prev), _get(stmts, "cash", prev)
        if pe_ is not None and pd_ is not None and invested is not None:
            prev_inv = pe_ + pd_ + (_get(stmts, "short_term_debt", prev) or 0) - (pc_ or 0)
            invested = (invested + prev_inv) / 2
    gross = g("gross_profit")
    if gross is None and revenue is not None and g("cost_of_revenue") is not None:
        gross = revenue - g("cost_of_revenue")
    return {
        "gross_margin": _div(gross, revenue),
        "operating_margin": _div(op, revenue),
        "net_margin": _div(ni, revenue),
        "fcf": fcf,
        "fcf_margin": _div(fcf, revenue),
        "fcf_conversion": _div(fcf, ni) if (ni and ni > 0) else None,
        "ebitda": ebitda,
        "roe": _div(ni, equity) if (equity and equity > 0) else None,
        "roa": _div(ni, g("total_assets")),
        "roic": _div(nopat, invested) if (invested and invested > 0) else None,
        "effective_tax_rate": tax_rate,
        "total_debt": debt,
        "net_debt": (debt - (cash or 0)) if debt is not None else None,
        "debt_to_equity": _div(debt, equity) if (equity and equity > 0) else None,
        "net_debt_to_ebitda": _div((debt - (cash or 0)) if debt is not None else None, ebitda) if (ebitda and ebitda > 0) else None,
        "interest_coverage": _div(op, g("interest_expense")) if (g("interest_expense") and g("interest_expense") > 0) else None,
        "current_ratio": _div(g("current_assets"), g("current_liabilities")),
        "capex_to_revenue": _div(capex, revenue),
        "sbc_to_revenue": _div(g("sbc"), revenue),
        "payout_ratio": _div(g("dividends_paid"), ni) if (ni and ni > 0) else None,
        "fcf_payout_ratio": _div(g("dividends_paid"), fcf) if (fcf and fcf > 0) else None,
        "accruals_ratio": _div((ni - ocf) if (ni is not None and ocf is not None) else None, g("total_assets")),
    }


def growth(stmts: Series, metric: str, years: int) -> float | None:
    """CAGR over ``years`` using the latest period and the period ``years`` earlier (requires positive endpoints)."""
    vals = stmts.get(metric, {})
    ps = sorted(vals)
    if len(ps) < years + 1:
        return None
    end, start = vals[ps[-1]], vals[ps[-1 - years]]
    if start is None or end is None or start <= 0 or end <= 0:
        return None
    return (end / start) ** (1 / years) - 1


def analyze(stmts: Series) -> dict:
    ps = periods(stmts)
    per = {}
    for i, p in enumerate(ps):
        per[p] = derived_for_period(stmts, p, ps[i - 1] if i > 0 else None)
    latest = ps[-1] if ps else None
    fcf_series = [per[p]["fcf"] for p in ps if per[p]["fcf"] is not None]
    shares = stmts.get("shares_outstanding", {})
    sp = sorted(shares)
    dilution = None
    if len(sp) >= 4 and shares[sp[-4]] and shares[sp[-4]] > 0:
        dilution = (shares[sp[-1]] / shares[sp[-4]]) ** (1 / 3) - 1
    summary = {
        "latest_period": latest,
        "n_periods": len(ps),
        "revenue_cagr_3y": growth(stmts, "revenue", 3),
        "revenue_cagr_5y": growth(stmts, "revenue", 5),
        "eps_cagr_3y": growth(stmts, "eps_diluted", 3),
        "fcf_positive_years": sum(1 for v in fcf_series if v > 0),
        "fcf_years": len(fcf_series),
        "fcf_consistency": (sum(1 for v in fcf_series if v > 0) / len(fcf_series)) if fcf_series else None,
        "share_count_cagr_3y": dilution,
        "roic_avg": _mean([per[p]["roic"] for p in ps[-5:]]),
        "operating_margin_avg": _mean([per[p]["operating_margin"] for p in ps[-5:]]),
        "operating_margin_stability": _stdev([per[p]["operating_margin"] for p in ps[-5:]]),
        **({f"latest_{k}": v for k, v in per[latest].items()} if latest else {}),
    }
    return {"periods": ps, "by_period": per, "summary": summary, "checks": consistency_checks(stmts)}


def _mean(xs):
    v = [x for x in xs if x is not None]
    return sum(v) / len(v) if v else None


def _stdev(xs):
    v = [x for x in xs if x is not None]
    if len(v) < 3:
        return None
    m = sum(v) / len(v)
    return math.sqrt(sum((x - m) ** 2 for x in v) / (len(v) - 1))


def consistency_checks(stmts: Series, tol: float = 0.02) -> list[dict]:
    """Basic integrity checks on reported data. Failures flag data issues; they are not accounting opinions."""
    out = []
    for p in periods(stmts):
        ta, tl, te = _get(stmts, "total_assets", p), _get(stmts, "total_liabilities", p), _get(stmts, "total_equity", p)
        if None not in (ta, tl, te) and ta:
            diff = abs(ta - (tl + te)) / abs(ta)
            out.append({"period": p, "check": "assets = liabilities + equity", "ok": diff <= tol,
                        "detail": f"relative difference {diff:.2%} (may include non-controlling interests)"})
        rev, gp = _get(stmts, "revenue", p), _get(stmts, "gross_profit", p)
        if rev is not None and gp is not None:
            out.append({"period": p, "check": "gross profit ≤ revenue", "ok": gp <= rev * (1 + 1e-9), "detail": ""})
        ca, tav = _get(stmts, "current_assets", p), _get(stmts, "total_assets", p)
        if ca is not None and tav is not None:
            out.append({"period": p, "check": "current assets ≤ total assets", "ok": ca <= tav * (1 + 1e-9), "detail": ""})
        ni, ocf = _get(stmts, "net_income", p), _get(stmts, "operating_cash_flow", p)
        if ni is not None and ocf is not None and tav:
            acc = (ni - ocf) / tav
            out.append({"period": p, "check": "accruals within ±10% of assets", "ok": abs(acc) <= 0.10,
                        "detail": f"(NI − OCF)/assets = {acc:.2%}; large accruals warrant review of earnings quality"})
    return out


def market_multiples(price: float | None, latest: Mapping[str, float | None], derived: Mapping[str, float | None]) -> dict:
    shares = latest.get("shares_outstanding")
    if price is None or not shares:
        return {"market_cap": None, "reason": "price or share count unavailable"}
    mcap = price * shares
    ev = mcap + (derived.get("net_debt") or 0) if derived.get("net_debt") is not None else None
    ni, rev, eq = latest.get("net_income"), latest.get("revenue"), latest.get("total_equity")
    return {
        "market_cap": mcap,
        "enterprise_value": ev,
        "pe": _div(mcap, ni) if (ni and ni > 0) else None,
        "ps": _div(mcap, rev),
        "pb": _div(mcap, eq) if (eq and eq > 0) else None,
        "ev_ebitda": _div(ev, derived.get("ebitda")) if (derived.get("ebitda") and derived["ebitda"] > 0) else None,
        "ev_sales": _div(ev, rev),
        "fcf_yield": _div(derived.get("fcf"), mcap),
        "earnings_yield": _div(ni, mcap),
        "dividend_yield": _div(latest.get("dividends_paid"), mcap),
    }


# ------------------------------------------------------------------ safe formula evaluation
_BIN = {ast.Add: operator.add, ast.Sub: operator.sub, ast.Mult: operator.mul, ast.Div: operator.truediv, ast.Pow: operator.pow}
_UN = {ast.USub: operator.neg, ast.UAdd: operator.pos}
_FUNCS = {"abs": abs, "min": min, "max": max, "sqrt": math.sqrt, "log": math.log}


class FormulaError(ValueError):
    pass


def parse_formula(expr: str) -> ast.AST:
    if len(expr) > 300:
        raise FormulaError("formula too long")
    try:
        tree = ast.parse(expr, mode="eval")
    except SyntaxError as e:
        raise FormulaError(f"syntax error: {e.msg}") from e
    for node in ast.walk(tree):
        if isinstance(node, (ast.Expression, ast.BinOp, ast.UnaryOp, ast.Constant, ast.Name, ast.Load, ast.Call)):
            pass
        elif type(node) in _BIN or type(node) in _UN:
            pass
        else:
            raise FormulaError(f"unsupported syntax: {type(node).__name__}")
        if isinstance(node, ast.Constant) and not isinstance(node.value, (int, float)):
            raise FormulaError("only numeric constants allowed")
        if isinstance(node, ast.Call) and not (isinstance(node.func, ast.Name) and node.func.id in _FUNCS):
            raise FormulaError("only abs/min/max/sqrt/log functions allowed")
    return tree


def formula_variables(expr: str) -> set[str]:
    tree = parse_formula(expr)
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)}
    return {n.id for n in ast.walk(tree) if isinstance(n, ast.Name)} - calls


def eval_formula(expr: str, variables: Mapping[str, float | None]) -> float | None:
    """Evaluate an arithmetic formula over named metrics. Returns None if any input is missing or math fails."""
    tree = parse_formula(expr)

    def ev(node):
        if isinstance(node, ast.Expression):
            return ev(node.body)
        if isinstance(node, ast.Constant):
            return float(node.value)
        if isinstance(node, ast.Name):
            if node.id not in variables:
                raise FormulaError(f"unknown variable '{node.id}'")
            v = variables[node.id]
            if v is None:
                raise _Missing()
            return float(v)
        if isinstance(node, ast.BinOp):
            return _BIN[type(node.op)](ev(node.left), ev(node.right))
        if isinstance(node, ast.UnaryOp):
            return _UN[type(node.op)](ev(node.operand))
        if isinstance(node, ast.Call):
            return _FUNCS[node.func.id](*[ev(a) for a in node.args])
        raise FormulaError("unsupported")

    try:
        out = ev(tree)
    except _Missing:
        return None
    except (ZeroDivisionError, ValueError, OverflowError):
        return None
    return out if math.isfinite(out) else None


class _Missing(Exception):
    pass
