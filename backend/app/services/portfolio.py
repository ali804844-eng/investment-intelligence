"""Portfolio service: state, history, performance (TWR/MWR), exposures, risk statistics, rebalancing."""
from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.finance import accounting as acc
from app.core.finance import returns as rt
from app.core.finance import risk_metrics as rm
from app.core.risk.engine import Limits, evaluate_portfolio
from app.models import FxRate, Portfolio, PortfolioSnapshot, PriceBar, Quote, RiskPolicy, Security, Transaction
from app.providers.sample import last_business_day
from app.services import market_data as md


def to_txn(t: Transaction) -> acc.Txn:
    return acc.Txn(id=t.id, type=t.type, trade_date=t.trade_date, security_id=t.security_id,
                   quantity=t.quantity, price=t.price, amount=t.amount, fees=t.fees or Decimal(0),
                   currency=t.currency, fx_rate=t.fx_rate or Decimal(1), split_ratio=t.split_ratio)


def load_txns(db: Session, pf: Portfolio) -> list[Transaction]:
    return list(db.scalars(select(Transaction).where(Transaction.portfolio_id == pf.id, Transaction.deleted_at.is_(None))
                           .order_by(Transaction.trade_date, Transaction.id)).all())


def _f(x) -> float | None:
    return None if x is None else float(x)


def limits_for(db: Session, user_id: int, portfolio_id: int | None) -> Limits:
    pol = None
    if portfolio_id is not None:
        pol = db.scalar(select(RiskPolicy).where(RiskPolicy.user_id == user_id, RiskPolicy.portfolio_id == portfolio_id))
    if pol is None:
        pol = db.scalar(select(RiskPolicy).where(RiskPolicy.user_id == user_id, RiskPolicy.portfolio_id.is_(None)))
    if pol is None:
        return Limits()
    return Limits(**{k: getattr(pol, k) for k in Limits.__dataclass_fields__})


def portfolio_state(db: Session, pf: Portfolio, txns: list[Transaction] | None = None) -> dict:
    txns = txns if txns is not None else load_txns(db, pf)
    ledger = acc.build_ledger([to_txn(t) for t in txns], pf.cost_basis_method)
    sec_ids = [sid for sid, p in ledger.positions.items() if p.quantity != 0]
    secs = {s.id: s for s in db.scalars(select(Security).where(Security.id.in_(sec_ids))).all()} if sec_ids else {}
    prices_base: dict[int, Decimal | None] = {}
    meta = {}
    warnings = []
    qualities = set()
    for sid in sec_ids:
        s = secs[sid]
        q = md.get_quote(db, s)
        fx = md.fx_rate(db, s.currency, pf.base_currency)
        if q["price"] is None:
            prices_base[sid] = None
            warnings.append(f"No price for {s.symbol}")
        elif fx is None:
            prices_base[sid] = None
            warnings.append(f"No FX rate {s.currency}->{pf.base_currency} for {s.symbol}")
        else:
            prices_base[sid] = Decimal(str(q["price"])) * Decimal(str(fx["rate"]))
            qualities.add(q["quality"])
            if fx["quality"] == "sample":
                qualities.add("sample")
        meta[sid] = {"quote": q, "fx": fx}
    val = acc.value_positions(ledger, prices_base)
    # Revalue cash at current FX (ledger.cash_base uses historical transaction FX).
    cash_val = Decimal(0)
    cash_ok = True
    for ccy, amt in ledger.cash_by_currency.items():
        fx = md.fx_rate(db, ccy, pf.base_currency)
        if fx is None:
            cash_ok = False
            warnings.append(f"No FX rate to value {ccy} cash")
            continue
        cash_val += amt * Decimal(str(fx["rate"]))
    if not cash_ok:
        cash_val = ledger.cash_base
    total = val["market_value"] + cash_val
    positions = []
    for row in val["positions"]:
        sid = row["security_id"]
        s = secs[sid]
        mv = row["market_value"]
        positions.append({
            "security_id": sid, "symbol": s.symbol, "name": s.name, "asset_class": s.asset_class, "sector": s.sector,
            "country": s.country, "currency": s.currency, "is_sample": s.is_sample,
            "quantity": _f(row["quantity"]), "avg_cost_base": _f(row["avg_cost"]), "cost_basis": _f(row["cost_basis"]),
            "price_local": meta[sid]["quote"]["price"], "price_base": _f(row["price"]), "market_value": _f(mv),
            "unrealized_pnl": _f(row["unrealized_pnl"]), "unrealized_pct": _f(row["unrealized_pct"]),
            "realized_pnl": _f(row["realized_pnl"]), "dividends": _f(row["dividends"]),
            "weight": float(mv / total) if (mv is not None and total) else None,
            "day_change_pct": meta[sid]["quote"].get("change_pct"),
            "price_as_of": meta[sid]["quote"].get("as_of"), "price_source": meta[sid]["quote"].get("source"),
            "quality": meta[sid]["quote"].get("quality"), "freshness": meta[sid]["quote"].get("freshness"),
            "fx_rate": (meta[sid]["fx"] or {}).get("rate"),
        })
    positions.sort(key=lambda p: -(p["market_value"] or 0))
    exp = _exposures(positions, float(cash_val), float(total) if total else 0.0)
    unreal = sum((p["unrealized_pnl"] or 0) for p in positions)
    stale = [p["symbol"] for p in positions if (p.get("freshness") or {}).get("status") != "fresh"]
    if stale:
        qualities.add("stale")
    has_sample = any(p["is_sample"] for p in positions)
    data_quality = "sample" if has_sample else ("stale" if stale else (sorted(qualities)[0] if qualities else "n/a"))
    net_contrib = float(ledger.net_contributions)
    total_f = float(total)
    return {
        "portfolio_id": pf.id, "name": pf.name, "kind": pf.kind, "base_currency": pf.base_currency,
        "is_hypothetical": pf.is_hypothetical, "cost_basis_method": pf.cost_basis_method,
        "total_value": total_f, "market_value": float(val["market_value"]), "cash": float(cash_val),
        "cash_by_currency": {k: float(v) for k, v in ledger.cash_by_currency.items()},
        "net_contributions": net_contrib, "invested_cost": sum((p["cost_basis"] or 0) for p in positions),
        "unrealized_pnl": unreal, "realized_pnl": float(ledger.realized_pnl), "income": float(ledger.total_income),
        "fees": float(ledger.total_fees),
        "total_pnl": (total_f - net_contrib) if net_contrib else None,
        "simple_return": ((total_f - net_contrib) / net_contrib) if net_contrib > 0 else None,
        "positions": positions, "exposures": exp, "closed_trades": ledger.closed_trades[-50:],
        "unpriced": [secs[s].symbol for s in val["unpriced_security_ids"]], "complete": val["complete"] and cash_ok,
        "warnings": warnings, "stale_positions": stale, "data_quality": data_quality,
        "has_sample_positions": has_sample, "has_real_positions": any(not p["is_sample"] for p in positions),
        "n_transactions": len(txns),
    }


def _exposures(positions: list[dict], cash: float, total: float) -> dict:
    out: dict[str, dict[str, float]] = {"sector": defaultdict(float), "country": defaultdict(float),
                                        "currency": defaultdict(float), "asset_class": defaultdict(float)}
    if total <= 0:
        return {k: {} for k in out}
    for p in positions:
        if p["market_value"] is None:
            continue
        w = p["market_value"] / total
        out["sector"][p["sector"] or "Unclassified"] += w
        out["country"][p["country"] or "N/A"] += w
        out["currency"][p["currency"]] += w
        out["asset_class"][p["asset_class"]] += w
    if cash:
        out["asset_class"]["cash"] += cash / total
    return {k: dict(sorted(v.items(), key=lambda kv: -kv[1])) for k, v in out.items()}


def _fx_series(db: Session, ccy: str, base: str, start: date) -> pd.Series | None:
    """Daily series: 1 unit ccy in base currency."""
    if ccy == base:
        return None

    def usd_to(c):
        if c == "USD":
            return None
        rows = db.execute(select(FxRate.date, FxRate.rate, FxRate.source).where(FxRate.base == "USD", FxRate.quote == c,
                                                                              FxRate.date >= start - timedelta(days=10))
                          .order_by(FxRate.date)).all()
        real = [(d, r) for d, r, s in rows if s != "sample"]
        use = real or [(d, r) for d, r, s in rows]
        return pd.Series([r for _, r in use], index=[d for d, _ in use]) if use else pd.Series(dtype=float)

    a, b = usd_to(ccy), usd_to(base)  # USD->ccy, USD->base
    if a is not None and a.empty or b is not None and b.empty:
        return pd.Series(dtype=float)
    if a is None:
        return b
    if b is None:
        return 1.0 / a
    j = a.index.union(b.index)
    return (b.reindex(j).ffill() / a.reindex(j).ffill()).dropna()


def portfolio_history(db: Session, pf: Portfolio, txns: list[Transaction] | None = None) -> dict:
    txns = txns if txns is not None else load_txns(db, pf)
    if not txns:
        return {"dates": [], "values": [], "metrics": {}, "methodology": "No transactions"}
    start = min(t.trade_date for t in txns)
    end = last_business_day()
    days = [d.date() for d in pd.bdate_range(start, end)]
    if not days:
        return {"dates": [], "values": [], "metrics": {}, "methodology": "No business days"}
    sec_ids = sorted({t.security_id for t in txns if t.security_id})
    secs = {s.id: s for s in db.scalars(select(Security).where(Security.id.in_(sec_ids))).all()} if sec_ids else {}
    px = {}
    for sid in sec_ids:
        rows = db.execute(select(PriceBar.date, PriceBar.close).where(PriceBar.security_id == sid,
                                                                     PriceBar.date >= start - timedelta(days=10))
                          .order_by(PriceBar.date)).all()
        s = pd.Series([r[1] for r in rows], index=[r[0] for r in rows], dtype=float)
        px[sid] = s.reindex(sorted(set(s.index) | set(days))).ffill().reindex(days)
    ccys = {secs[s].currency for s in sec_ids} | {t.currency for t in txns}
    fx = {}
    for c in ccys:
        if c == pf.base_currency:
            continue
        s = _fx_series(db, c, pf.base_currency, start)
        fx[c] = s.reindex(sorted(set(s.index) | set(days))).ffill().reindex(days) if s is not None and not s.empty else None
    implicit = not any(t.type in ("deposit", "withdrawal") for t in txns)
    by_day: dict[date, list[Transaction]] = defaultdict(list)
    for t in sorted(txns, key=lambda x: (x.trade_date, x.id)):
        eff = t.trade_date
        while eff.weekday() >= 5:  # weekend-dated entries take effect on the next business day
            eff += timedelta(days=1)
        by_day[eff].append(t)
    qty: dict[int, float] = defaultdict(float)
    cash: dict[str, float] = defaultdict(float)
    values, flows, missing_days = [], [], 0
    for i, d in enumerate(days):
        flow = 0.0
        todays = by_day.get(d, [])
        for t in todays:
            fxr = float(t.fx_rate or 1)
            q = float(t.quantity or 0)
            p = float(t.price or 0)
            fee = float(t.fees or 0)
            amt = float(t.amount or 0)
            if t.type == "deposit":
                cash[t.currency] += amt
                flow += amt * fxr
            elif t.type == "withdrawal":
                cash[t.currency] -= amt
                flow -= amt * fxr
            elif t.type == "buy":
                qty[t.security_id] += q
                cash[t.currency] -= q * p + fee
                if implicit:
                    flow += (q * p + fee) * fxr
            elif t.type == "sell":
                qty[t.security_id] -= q
                cash[t.currency] += q * p - fee
                if implicit:
                    flow -= (q * p - fee) * fxr
            elif t.type in ("dividend", "interest"):
                cash[t.currency] += amt - fee
            elif t.type == "fee":
                cash[t.currency] -= amt
            elif t.type == "split":
                qty[t.security_id] *= float(t.split_ratio or 1)
        v = 0.0
        miss = False
        for sid, qn in qty.items():
            if abs(qn) < 1e-12:
                continue
            p = px[sid].iloc[i] if sid in px else np.nan
            c = secs[sid].currency
            f = 1.0 if c == pf.base_currency else (fx[c].iloc[i] if fx.get(c) is not None else np.nan)
            if np.isnan(p) or np.isnan(f):
                miss = True
                continue
            v += qn * p * f
        for c, amt in cash.items():
            f = 1.0 if c == pf.base_currency else (fx[c].iloc[i] if fx.get(c) is not None else np.nan)
            v += amt * (0 if np.isnan(f) else f)
        missing_days += miss
        values.append(v)
        flows.append(flow)
    twr_daily = rt.daily_twr_series(values, flows)
    index = [1.0]
    for r in twr_daily[1:]:
        index.append(index[-1] * (1 + (r or 0.0)))
    daily = [r for r in twr_daily[1:] if r is not None]
    total_twr = index[-1] - 1
    span = (days[-1] - days[0]).days
    cfs = [(d, -f) for d, f in zip(days, flows, strict=True) if f] + [(days[-1], values[-1])]
    mwr = rt.xirr(cfs)
    mdd = rm.max_drawdown(index)
    peak = max(index)
    metrics = {
        "twr_total": total_twr, "twr_annualized": rt.annualize(total_twr, span) if span >= 365 else None,
        "mwr_annualized": mwr, "volatility": rm.annualized_volatility(daily), "sharpe": rm.sharpe_ratio(daily),
        "sortino": rm.sortino_ratio(daily), "max_drawdown": (mdd or {}).get("max_drawdown"),
        "current_drawdown": index[-1] / peak - 1 if peak > 0 else None,
        "var_95_1d": rm.historical_var(daily, 0.95),
        "days": len(days), "days_with_missing_prices": int(missing_days),
    }
    periods = {}
    for label, delta in (("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365), ("3Y", 365 * 3)):
        s = days[-1] - timedelta(days=delta)
        periods[label] = rt.period_return(days, index, s) if days[0] <= s else None
    ytd_start = date(days[-1].year - 1, 12, 31)
    periods["YTD"] = rt.period_return(days, index, ytd_start) if days[0] <= ytd_start else None
    periods["Inception"] = total_twr
    bench = None
    if pf.benchmark_security_id:
        bsec = db.get(Security, pf.benchmark_security_id)
        ds, cl = md.close_series(db, pf.benchmark_security_id, start - timedelta(days=10))
        if cl:
            bs = pd.Series(cl, index=ds).reindex(sorted(set(ds) | set(days))).ffill().reindex(days)
            if bs.notna().any():
                first = bs.dropna().iloc[0]
                bvals = list((bs / first).ffill().fillna(1.0).values)
                bd = [bvals[i] / bvals[i - 1] - 1 for i in range(1, len(bvals))]
                n = min(len(daily), len(bd))
                bench = {"symbol": bsec.symbol, "is_sample": bsec.is_sample, "values": bvals,
                         "total_return": bvals[-1] - 1,
                         "periods": {lbl: (rt.period_return(days, bvals, days[-1] - timedelta(days=dl)) if days[0] <= days[-1] - timedelta(days=dl) else None)
                                     for lbl, dl in (("1M", 30), ("3M", 91), ("6M", 182), ("1Y", 365), ("3Y", 1095))}}
                metrics["beta"] = rm.beta(daily[-n:], bd[-n:]) if n else None
                metrics["tracking_error"] = rm.tracking_error(daily[-n:], bd[-n:]) if n else None
                metrics["excess_return_total"] = total_twr - (bvals[-1] - 1)
    step = max(1, len(days) // 800)
    return {
        "dates": [d.isoformat() for d in days][::step], "values": values[::step], "twr_index": index[::step],
        "drawdown": rm.drawdown_series(index)[::step], "benchmark": ({**bench, "values": bench["values"][::step]} if bench else None),
        "metrics": metrics, "periods": periods,
        "methodology": ("Time-weighted return links daily returns after removing external cash flows (assumed at start "
                        "of day). Money-weighted return is the XIRR of external flows plus ending value. "
                        + ("No deposits recorded: buys/sells are treated as external flows (positions-only view). " if implicit else "")
                        + "Prices are end-of-day closes, forward-filled over gaps; FX converted with daily reference rates."),
        "is_hypothetical": pf.is_hypothetical,
    }


def correlation_stats(db: Session, sec_ids: list[int], lookback_days: int = 365) -> dict:
    start = last_business_day() - timedelta(days=lookback_days)
    series = {}
    for sid in sec_ids:
        ds, cl = md.close_series(db, sid, start)
        if len(cl) > 60:
            series[sid] = pd.Series(cl, index=ds)
    if len(series) < 2:
        return {"matrix": [], "symbols": [], "high": []}
    df = pd.DataFrame(series).sort_index().ffill().pct_change(fill_method=None).dropna(how="all")
    corr = df.corr(min_periods=60)
    syms = {s.id: s.symbol for s in db.scalars(select(Security).where(Security.id.in_(list(series)))).all()}
    cols = list(corr.columns)
    return {"symbols": [syms[c] for c in cols],
            "matrix": [[None if pd.isna(corr.iloc[i, j]) else round(float(corr.iloc[i, j]), 3) for j in range(len(cols))] for i in range(len(cols))],
            "cov_annual": (df.cov(min_periods=60) * 252).fillna(0.0), "ids": cols, "syms": syms}


def risk_report(db: Session, pf: Portfolio, user_id: int) -> dict:
    state = portfolio_state(db, pf)
    hist = portfolio_history(db, pf)
    limits = limits_for(db, user_id, pf.id)
    ids = [p["security_id"] for p in state["positions"]]
    cs = correlation_stats(db, ids)
    high = []
    if cs["matrix"]:
        n = len(cs["symbols"])
        for i in range(n):
            for j in range(i + 1, n):
                c = cs["matrix"][i][j]
                if c is not None and c > limits.max_pairwise_corr:
                    high.append({"a": cs["symbols"][i], "b": cs["symbols"][j], "corr": c})
    ex_ante_vol = None
    if cs.get("ids") and state["total_value"]:
        wmap = {p["security_id"]: (p["weight"] or 0) for p in state["positions"]}
        w = [wmap.get(i, 0) for i in cs["ids"]]
        ex_ante_vol = rm.portfolio_volatility(w, cs["cov_annual"].values)
    stats = {"current_drawdown": hist["metrics"].get("current_drawdown"),
             "volatility": hist["metrics"].get("volatility"), "high_correlations": high}
    checks = evaluate_portfolio(state, limits, stats)
    weights = {p["symbol"]: p["weight"] or 0 for p in state["positions"]}
    return {
        "portfolio_id": pf.id, "limits": limits.__dict__, "checks": checks,
        "breaches": [c for c in checks if c["status"] == "breach"],
        "warnings": [c for c in checks if c["status"] == "warning"],
        "concentration": rm.concentration(weights), "ex_ante_volatility": ex_ante_vol,
        "realized": {k: hist["metrics"].get(k) for k in ("volatility", "max_drawdown", "current_drawdown", "var_95_1d", "beta", "sharpe")},
        "correlation": {"symbols": cs["symbols"], "matrix": cs["matrix"]},
        "exposures": state["exposures"], "data_quality": state["data_quality"],
        "engine": "deterministic risk engine (independent of the AI agent)",
    }


def rebalance_simulation(state: dict, targets: dict[str, float], cost_bps: float = 15.0) -> dict:
    total = state["total_value"]
    if total <= 0:
        raise ValueError("Portfolio has no value")
    if sum(targets.values()) > 1.0 + 1e-9:
        raise ValueError("Target weights sum to more than 100%")
    cur = {p["symbol"]: p for p in state["positions"]}
    trades = []
    turnover = 0.0
    for sym in sorted(set(cur) | set(targets)):
        cw = (cur.get(sym) or {}).get("weight") or 0.0
        tw = targets.get(sym, 0.0)
        delta = (tw - cw) * total
        px = (cur.get(sym) or {}).get("price_base")
        trades.append({"symbol": sym, "current_weight": cw, "target_weight": tw, "trade_value": delta,
                       "approx_quantity": (delta / px) if px else None, "side": "buy" if delta > 0 else ("sell" if delta < 0 else "hold")})
        turnover += abs(delta)
    cost = turnover * cost_bps / 10_000
    return {"trades": [t for t in trades if abs(t["trade_value"]) > 0.005 * total or t["side"] == "hold"],
            "turnover_value": turnover, "turnover_pct": turnover / total, "estimated_costs": cost,
            "post_trade_cash_weight": 1 - sum(targets.values()),
            "note": f"Simulation only. Costs assume {cost_bps} bps per unit traded; taxes not modelled; "
                    "symbols without a current price need a quote before quantities can be computed."}


def write_snapshot(db: Session, pf: Portfolio) -> PortfolioSnapshot | None:
    st = portfolio_state(db, pf)
    d = last_business_day()
    snap = db.scalar(select(PortfolioSnapshot).where(PortfolioSnapshot.portfolio_id == pf.id, PortfolioSnapshot.date == d))
    if snap is None:
        snap = PortfolioSnapshot(portfolio_id=pf.id, date=d)
        db.add(snap)
    snap.market_value = st["market_value"]
    snap.cash = st["cash"]
    snap.data_quality = st["data_quality"]
    snap.details = {"total_value": st["total_value"], "weights": {p["symbol"]: p["weight"] for p in st["positions"]},
                    "unrealized_pnl": st["unrealized_pnl"], "realized_pnl": st["realized_pnl"]}
    db.flush()
    return snap


def held_quantity(state: dict, security_id: int) -> float:
    for p in state["positions"]:
        if p["security_id"] == security_id:
            return p["quantity"] or 0.0
    return 0.0


def quote_age_ok(db: Session, sec: Security, max_days: int) -> dict:
    q = db.get(Quote, sec.id)
    return md.freshness(q.as_of if q else None, max_days)
