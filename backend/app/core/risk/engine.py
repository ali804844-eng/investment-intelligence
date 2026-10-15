"""Independent, deterministic risk engine.

These checks run on numbers only. They do not consult the language model, and generated text cannot
override them. The agent may *read* results; it cannot change limits (limits are user-editable only via the
authenticated settings API, which is audited).
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class Limits:
    max_position_pct: float = 0.10
    max_sector_pct: float = 0.30
    max_country_pct: float = 0.70
    max_drawdown_pct: float = 0.20
    max_annual_vol: float = 0.25
    min_avg_dollar_volume: float = 1_000_000.0
    max_pct_of_adv: float = 0.05
    stale_price_days: int = 5
    stale_fundamentals_days: int = 200
    max_pairwise_corr: float = 0.85


def _check(rule: str, value, limit, ok: bool, message: str, severity: str = "breach") -> dict:
    return {"rule": rule, "value": value, "limit": limit, "status": "pass" if ok else severity, "message": message}


def evaluate_portfolio(state: dict, limits: Limits, stats: dict | None = None) -> list[dict]:
    """state: output of portfolio service (positions with weight, sector, country, freshness)."""
    out = []
    stats = stats or {}
    for p in state.get("positions", []):
        w = p.get("weight")
        if w is None:
            continue
        out.append(_check("max_position_pct", w, limits.max_position_pct, w <= limits.max_position_pct + 1e-9,
                          f"{p['symbol']} is {w:.1%} of portfolio (limit {limits.max_position_pct:.0%})"))
        fr = (p.get("freshness") or {})
        if fr.get("status") in ("stale", "missing"):
            out.append(_check("stale_price", fr.get("age_business_days"), limits.stale_price_days, False,
                              f"{p['symbol']} price is {fr.get('status')}; valuation may be wrong", "warning"))
    for sector, w in (state.get("exposures", {}).get("sector") or {}).items():
        out.append(_check("max_sector_pct", w, limits.max_sector_pct, w <= limits.max_sector_pct + 1e-9,
                          f"Sector '{sector}' is {w:.1%} (limit {limits.max_sector_pct:.0%})"))
    for country, w in (state.get("exposures", {}).get("country") or {}).items():
        out.append(_check("max_country_pct", w, limits.max_country_pct, w <= limits.max_country_pct + 1e-9,
                          f"Country '{country}' is {w:.1%} (limit {limits.max_country_pct:.0%})", "warning"))
    dd = stats.get("current_drawdown")
    if dd is not None:
        out.append(_check("max_drawdown_pct", dd, -limits.max_drawdown_pct, dd >= -limits.max_drawdown_pct,
                          f"Current drawdown {dd:.1%} (limit −{limits.max_drawdown_pct:.0%})"))
    vol = stats.get("volatility")
    if vol is not None:
        out.append(_check("max_annual_vol", vol, limits.max_annual_vol, vol <= limits.max_annual_vol,
                          f"Annualized volatility {vol:.1%} (limit {limits.max_annual_vol:.0%})", "warning"))
    for pair in stats.get("high_correlations", []):
        out.append(_check("max_pairwise_corr", pair["corr"], limits.max_pairwise_corr, False,
                          f"{pair['a']} and {pair['b']} correlation {pair['corr']:.2f} exceeds {limits.max_pairwise_corr:.2f}",
                          "warning"))
    if state.get("unpriced"):
        out.append(_check("data_completeness", len(state["unpriced"]), 0, False,
                          f"{len(state['unpriced'])} position(s) have no price; totals are incomplete", "warning"))
    return out


def pre_trade_check(*, side: str, quantity: float, price: float | None, symbol: str, sector: str | None,
                    country: str | None, portfolio_kind: str, portfolio_is_hypothetical: bool, security_is_sample: bool,
                    state: dict, limits: Limits, price_freshness: dict,
                    adv_usd: float | None, fx_to_base: float | None, held_quantity: float,
                    live_trading_enabled: bool = False) -> dict:
    """Independent pre-trade check for paper/manual trades proposed by the user or the agent."""
    checks = []

    def add(rule, ok, msg, sev="breach", value=None, limit=None):
        checks.append(_check(rule, value, limit, ok, msg, sev))

    if portfolio_kind == "live":
        add("live_trading_gate", False,
            "Live order execution is not implemented. Orders cannot be sent to a broker from this application.")
    add("quantity_positive", quantity > 0, "Quantity must be positive", value=quantity)
    add("price_available", price is not None and price > 0, "A valid price is required", value=price)
    add("fresh_price", price_freshness.get("status") == "fresh",
        f"Price data is {price_freshness.get('status')} (age {price_freshness.get('age_business_days')} business days); "
        "stale prices are rejected", value=price_freshness.get("age_business_days"), limit=limits.stale_price_days)
    if security_is_sample and not portfolio_is_hypothetical:
        add("sample_isolation", False, "SAMPLE instruments may only be used in hypothetical portfolios")
    if security_is_sample and state.get("has_real_positions"):
        add("sample_isolation", False, "This portfolio holds real instruments; SAMPLE instruments must be kept separate")
    if not security_is_sample and state.get("has_sample_positions"):
        add("sample_isolation", False, "This portfolio holds SAMPLE instruments; keep real instruments in a separate portfolio")
    if side == "sell":
        add("sufficient_holdings", held_quantity >= quantity - 1e-12,
            f"Cannot sell {quantity} {symbol}: only {held_quantity} held (short selling not supported)",
            value=held_quantity, limit=quantity)
    if price and fx_to_base and quantity > 0:
        notional = quantity * price * fx_to_base
        total = state.get("total_value") or 0.0
        cur_mv = 0.0
        for p in state.get("positions", []):
            if p.get("symbol") == symbol and p.get("market_value") is not None:
                cur_mv = float(p["market_value"])
        new_mv = cur_mv + notional if side == "buy" else cur_mv - notional
        if side == "buy":
            cash = state.get("cash") or 0.0
            add("cash_available", cash >= notional - 1e-6,
                f"Order needs {notional:,.2f} but only {cash:,.2f} cash is available", value=cash, limit=notional)
        if total > 0:
            w = new_mv / total
            add("max_position_pct", w <= limits.max_position_pct + 1e-9,
                f"Post-trade weight of {symbol} would be {w:.1%} (limit {limits.max_position_pct:.0%})",
                value=w, limit=limits.max_position_pct)
            if sector and side == "buy":
                sw = (state.get("exposures", {}).get("sector") or {}).get(sector, 0.0) + notional / total
                add("max_sector_pct", sw <= limits.max_sector_pct + 1e-9,
                    f"Post-trade '{sector}' exposure would be {sw:.1%} (limit {limits.max_sector_pct:.0%})",
                    value=sw, limit=limits.max_sector_pct)
        if adv_usd is not None:
            add("liquidity_min_adv", adv_usd >= limits.min_avg_dollar_volume,
                f"Average daily dollar volume {adv_usd:,.0f} below minimum {limits.min_avg_dollar_volume:,.0f}",
                "warning", adv_usd, limits.min_avg_dollar_volume)
            pct = notional / adv_usd if adv_usd > 0 else None
            if pct is not None:
                add("liquidity_pct_adv", pct <= limits.max_pct_of_adv,
                    f"Order is {pct:.1%} of average daily volume (limit {limits.max_pct_of_adv:.0%})",
                    "warning", pct, limits.max_pct_of_adv)
        else:
            add("liquidity_known", False, "Liquidity unknown (no volume data)", "warning")
    approved = all(c["status"] != "breach" for c in checks)
    return {"approved": approved, "checks": checks,
            "summary": "All hard limits pass" if approved else "Blocked by: " + ", ".join(
                c["rule"] for c in checks if c["status"] == "breach")}


STRESS_SCENARIOS = {
    "equity_crash": {"title": "Equity crash (−30% equities, −10% credit, +5% long bonds, +8% gold)",
                     "shocks": {"equity": -0.30, "etf": -0.25, "index": -0.30, "bond": 0.02, "commodity": 0.0,
                                "crypto": -0.50, "fx": 0.0},
                     "sector_overrides": {"Precious Metals": 0.08, "Fixed Income": 0.03, "Utilities": -0.15,
                                          "Consumer Staples": -0.15}},
    "rate_shock": {"title": "Rate shock (+200bp; long-duration assets hit)",
                   "shocks": {"equity": -0.12, "etf": -0.10, "index": -0.12, "bond": -0.10, "commodity": -0.03,
                              "crypto": -0.25, "fx": 0.0},
                   "sector_overrides": {"Real Estate": -0.22, "Utilities": -0.15, "Technology": -0.18, "Financials": 0.0}},
    "stagflation": {"title": "Stagflation (equities −18%, commodities +15%)",
                    "shocks": {"equity": -0.18, "etf": -0.15, "index": -0.18, "bond": -0.08, "commodity": 0.15,
                               "crypto": -0.30, "fx": 0.0},
                    "sector_overrides": {"Energy": 0.10, "Materials": 0.0, "Consumer Discretionary": -0.28}},
    "usd_rally": {"title": "USD +10% vs all currencies (translation effect on foreign holdings)",
                  "shocks": {}, "fx_shock": -0.10},
}


def stress_test(positions: list[dict], base_currency: str, scenario_key: str, custom: dict | None = None) -> dict:
    sc = custom or STRESS_SCENARIOS[scenario_key]
    rows = []
    total_before = sum(p["market_value"] for p in positions if p.get("market_value") is not None)
    total_after = 0.0
    for p in positions:
        mv = p.get("market_value")
        if mv is None:
            continue
        shock = sc.get("sector_overrides", {}).get(p.get("sector") or "", None)
        if shock is None:
            shock = sc.get("shocks", {}).get(p.get("asset_class") or "equity", 0.0)
        if sc.get("fx_shock") and p.get("currency") and p["currency"] != base_currency:
            shock = (1 + shock) * (1 + sc["fx_shock"]) - 1
        after = mv * (1 + shock)
        total_after += after
        rows.append({"symbol": p["symbol"], "market_value": mv, "shock": shock, "pnl": after - mv})
    return {"scenario": scenario_key, "title": sc.get("title", scenario_key), "positions": rows,
            "invested_before": total_before, "invested_after": total_after,
            "pnl": total_after - total_before,
            "pct": (total_after / total_before - 1) if total_before else None,
            "note": "Hypothetical shocks, not forecasts. Cash is assumed unaffected."}


def position_size(equity: float, entry: float, stop: float, risk_per_trade_pct: float = 0.01,
                  max_position_pct: float = 0.10) -> dict:
    """Fixed-fractional sizing: risk a set % of equity between entry and stop, capped by the position limit."""
    if entry <= 0 or stop <= 0 or stop >= entry:
        raise ValueError("Long sizing requires 0 < stop < entry")
    risk_per_share = entry - stop
    by_risk = equity * risk_per_trade_pct / risk_per_share
    by_cap = equity * max_position_pct / entry
    shares = min(by_risk, by_cap)
    return {"shares": shares, "notional": shares * entry, "risk_amount": shares * risk_per_share,
            "binding_constraint": "risk_per_trade" if by_risk <= by_cap else "max_position_pct",
            "formula": "shares = min(equity·risk% / (entry − stop), equity·max_position% / entry)"}
