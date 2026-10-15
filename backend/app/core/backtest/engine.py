"""Cross-sectional strategy backtester.

Design choices that guard against common biases:
  * Point-in-time features (see ``Panel``): fundamentals only after their filing date, prices only up to the
    signal date.
  * Execution lag: signals computed on the close of the rebalance date are executed at the close of the NEXT
    trading day.
  * Transaction costs: commission + half-spread + slippage (bps) charged on traded notional (turnover).
  * Benchmark-relative statistics are always reported next to absolute ones.
  * Survivorship: the universe is whatever exists in the database; if it contains no delisted securities,
    a warning is attached because survivorship bias cannot be ruled out.
  * Walk-forward: parameters chosen on a training window are evaluated on the following unseen window; only
    the stitched out-of-sample segments are used for the headline OOS metrics.
"""
from __future__ import annotations

import copy
import math
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

from app.core.finance import risk_metrics
from app.core.screening.engine import run_screen
from app.services.panel import Panel, month_ends


@dataclass
class Costs:
    commission_bps: float = 1.0
    spread_bps: float = 10.0
    slippage_bps: float = 5.0

    @property
    def one_way_bps(self) -> float:
        return self.commission_bps + self.spread_bps / 2 + self.slippage_bps


def run_backtest(panel: Panel, params: dict, start: date, end: date, benchmark_id: int | None = None,
                 costs: Costs | None = None, top_n: int | None = None, rebalance_months: int = 1,
                 universe_ids: list[int] | None = None, record_holdings: bool = True) -> dict:
    costs = costs or Costs()
    top_n = top_n or int(params.get("top_n", 8))
    px = panel.close.sort_index()
    px = px.loc[(px.index >= start) & (px.index <= end)]
    if px.empty or len(px) < 30:
        raise ValueError("Not enough price history in the requested window")
    dates = list(px.index)
    rets = px.pct_change(fill_method=None)
    rebal = month_ends(dates, start, end)[::rebalance_months]
    rebal = [d for d in rebal if d < dates[-1]]
    if not rebal:
        raise ValueError("No rebalance dates in window")
    ids = universe_ids or [sid for sid, m in panel.securities.items() if m.asset_class in params.get("universe", {}).get("asset_classes", ["equity"])]
    exec_at: dict[date, date] = {}
    for d in rebal:
        i = dates.index(d)
        if i + 1 < len(dates):
            exec_at[dates[i + 1]] = d
    weights = pd.Series(dtype=float)  # current (drifted) weights by sid
    equity = 1.0
    curve = []
    holdings_log = []
    turnover_list = []
    total_cost = 0.0
    warnings: list[str] = []
    pending: dict | None = None
    n_held = []
    for t, d in enumerate(dates):
        # 1) apply today's returns to existing weights
        if t > 0 and len(weights):
            r = rets.loc[d, weights.index].fillna(0.0)
            if rets.loc[d, weights.index].isna().any():
                missing = [panel.securities[s].symbol for s in weights.index[rets.loc[d, weights.index].isna()]]
                if len(warnings) < 20:
                    warnings.append(f"{d}: missing price for {', '.join(missing)}; return assumed 0 for that day")
            port_r = float((weights * r).sum())
            equity *= 1 + port_r
            grown = weights * (1 + r)
            cash_w = 1 - weights.sum()
            tot = grown.sum() + cash_w
            weights = grown / tot if tot > 0 else grown
        # 2) execute pending rebalance at today's close
        if d in exec_at and pending is not None:
            target = pending["target"]
            all_ids = sorted(set(weights.index) | set(target.index))
            cur = weights.reindex(all_ids).fillna(0.0)
            tgt = target.reindex(all_ids).fillna(0.0)
            turnover = float((tgt - cur).abs().sum())
            cost = turnover * costs.one_way_bps / 10_000
            equity *= 1 - cost
            total_cost += cost
            turnover_list.append(turnover)
            weights = tgt[tgt > 0]
            n_held.append(len(weights))
            if record_holdings:
                holdings_log.append({"signal_date": pending["signal_date"].isoformat(), "exec_date": d.isoformat(),
                                     "holdings": [panel.securities[s].symbol for s in weights.index],
                                     "turnover": round(turnover, 4), "cost": round(cost, 6)})
            pending = None
        # 3) compute signal at rebalance date close (executed next day)
        if d in rebal:
            res = run_screen(panel, d, params, security_ids=ids)
            chosen = [c["security_id"] for c in res["candidates"][:top_n]]
            target = pd.Series({s: 1.0 / len(chosen) for s in chosen}) if chosen else pd.Series(dtype=float)
            pending = {"target": target, "signal_date": d}
        curve.append((d, equity))
    eq = pd.Series([v for _, v in curve], index=[d for d, _ in curve])
    bench = None
    if benchmark_id is not None and benchmark_id in panel.close.columns:
        b = panel.close[benchmark_id].reindex(eq.index).ffill()
        if b.notna().sum() > 10:
            b = b / b.dropna().iloc[0]
            bench = b
    metrics = compute_metrics(eq, bench, turnover_list, total_cost, rebalance_months)
    metrics["avg_holdings"] = float(np.mean(n_held)) if n_held else 0.0
    if not any(not m.is_sample for m in panel.securities.values()):
        warnings.insert(0, "Backtest uses SAMPLE (synthetic) data: results illustrate the method only.")
    warnings.append("Universe = securities currently in the database; no delisted securities are included, so "
                    "survivorship bias cannot be ruled out.")
    if metrics.get("years", 0) < 3:
        warnings.append("Short test window (<3 years); statistics are unreliable.")
    if metrics["avg_holdings"] and metrics["avg_holdings"] < 5:
        warnings.append("Few holdings on average; results are concentrated and noisy.")
    return {
        "equity_curve": [(d.isoformat(), round(v, 6)) for d, v in eq.items()][:: max(1, len(eq) // 600)],
        "benchmark_curve": [(d.isoformat(), round(float(v), 6)) for d, v in bench.items()][:: max(1, len(eq) // 600)] if bench is not None else [],
        "metrics": metrics,
        "rebalances": holdings_log,
        "warnings": warnings,
        "_equity": eq,
        "_bench": bench,
    }


def compute_metrics(eq: pd.Series, bench: pd.Series | None, turnovers: list[float], total_cost: float,
                    rebalance_months: int = 1) -> dict:
    vals = eq.values
    days = (eq.index[-1] - eq.index[0]).days
    years = days / 365.25
    total = vals[-1] / vals[0] - 1
    daily = list(eq.pct_change().dropna().values)
    m = {
        "start": eq.index[0].isoformat(), "end": eq.index[-1].isoformat(), "years": round(years, 2),
        "total_return": total,
        "cagr": (1 + total) ** (1 / years) - 1 if years > 0 and total > -1 else None,
        "volatility": risk_metrics.annualized_volatility(daily),
        "sharpe": risk_metrics.sharpe_ratio(daily),
        "sortino": risk_metrics.sortino_ratio(daily),
        "max_drawdown": (risk_metrics.max_drawdown(list(vals)) or {}).get("max_drawdown"),
        "avg_turnover_per_rebalance": float(np.mean(turnovers)) if turnovers else 0.0,
        "annual_turnover": float(np.sum(turnovers) / years) if years > 0 else None,
        "total_costs": total_cost,
        "n_rebalances": len(turnovers),
    }
    if m["cagr"] is not None and m["max_drawdown"]:
        m["calmar"] = m["cagr"] / abs(m["max_drawdown"]) if m["max_drawdown"] < 0 else None
    if bench is not None:
        bv = bench.values
        btot = bv[-1] / bv[0] - 1
        bd = list(bench.pct_change().dropna().values)
        m["benchmark_total_return"] = btot
        m["benchmark_cagr"] = (1 + btot) ** (1 / years) - 1 if years > 0 and btot > -1 else None
        m["benchmark_volatility"] = risk_metrics.annualized_volatility(bd)
        m["benchmark_max_drawdown"] = (risk_metrics.max_drawdown(list(bv)) or {}).get("max_drawdown")
        m["benchmark_sharpe"] = risk_metrics.sharpe_ratio(bd)
        if m["cagr"] is not None and m["benchmark_cagr"] is not None:
            m["excess_cagr"] = m["cagr"] - m["benchmark_cagr"]
        n = min(len(daily), len(bd))
        te = risk_metrics.tracking_error(daily[-n:], bd[-n:])
        m["tracking_error"] = te
        m["information_ratio"] = (m.get("excess_cagr") / te) if (te and m.get("excess_cagr") is not None) else None
        m["beta"] = risk_metrics.beta(daily[-n:], bd[-n:])
        eqd = pd.Series(eq.values, index=pd.to_datetime(list(eq.index)))
        bd_ = pd.Series(bench.values, index=pd.to_datetime(list(bench.index)))
        me = eqd.resample("ME").last().pct_change().dropna()
        mb = bd_.resample("ME").last().pct_change().dropna()
        j = me.index.intersection(mb.index)
        m["monthly_hit_rate_vs_benchmark"] = float((me[j] > mb[j]).mean()) if len(j) else None
    return {k: (None if (isinstance(v, float) and not math.isfinite(v)) else (float(v) if isinstance(v, (np.floating,)) else v))
            for k, v in m.items()}


def _with_overrides(params: dict, overrides: dict) -> dict:
    p = copy.deepcopy(params)
    for k, v in overrides.items():
        if k == "criteria_values":  # {feature: new threshold}
            for c in p["criteria"]:
                if c["feature"] in v:
                    c["value"] = v[c["feature"]]
        else:
            p[k] = v
    return p


def walk_forward(panel: Panel, params: dict, start: date, end: date, train_years: int = 3, test_years: int = 1,
                 grid: list[dict] | None = None, benchmark_id: int | None = None, costs: Costs | None = None,
                 select_metric: str = "sharpe") -> dict:
    """Rolling walk-forward. ``grid`` is a list of parameter overrides; the variant with the best training
    ``select_metric`` is evaluated on the next test window. Without a grid, the fixed params are evaluated on each
    test window (a robustness check across periods)."""
    grid = grid or [{}]
    folds = []
    oos_parts: list[pd.Series] = []
    bench_parts: list[pd.Series] = []
    fold_start = start
    while True:
        train_end = _add_years(fold_start, train_years)
        test_end = _add_years(train_end, test_years)
        if test_end > end:
            break
        best = None
        train_scores = []
        for i, ov in enumerate(grid):
            p = _with_overrides(params, ov)
            try:
                r = run_backtest(panel, p, fold_start, train_end, benchmark_id, costs, record_holdings=False)
            except ValueError:
                continue
            score = r["metrics"].get(select_metric)
            train_scores.append({"variant": i, "overrides": ov, select_metric: score})
            if score is not None and (best is None or score > best[0]):
                best = (score, i, ov)
        if best is None:
            break
        p = _with_overrides(params, best[2])
        r = run_backtest(panel, p, train_end, test_end, benchmark_id, costs, record_holdings=False)
        eq = r["_equity"]
        folds.append({"train": [fold_start.isoformat(), train_end.isoformat()],
                      "test": [train_end.isoformat(), test_end.isoformat()],
                      "chosen_variant": best[1], "chosen_overrides": best[2], "train_scores": train_scores,
                      "test_metrics": {k: r["metrics"].get(k) for k in ("total_return", "cagr", "sharpe", "max_drawdown",
                                                                         "excess_cagr", "benchmark_total_return", "annual_turnover")}})
        oos_parts.append(eq / eq.iloc[0])
        if r["_bench"] is not None:
            bench_parts.append(r["_bench"] / r["_bench"].iloc[0])
        fold_start = _add_years(fold_start, test_years)
    if not folds:
        raise ValueError("Window too short for the requested walk-forward configuration")
    stitched = _stitch(oos_parts)
    bstitched = _stitch(bench_parts) if len(bench_parts) == len(oos_parts) else None
    metrics = compute_metrics(stitched, bstitched, [], 0.0)
    for k in ("avg_turnover_per_rebalance", "annual_turnover", "total_costs", "n_rebalances"):
        metrics[k] = None  # reported per fold; costs are already inside each fold's returns
    beat = sum(1 for f in folds if (f["test_metrics"].get("excess_cagr") or 0) > 0)
    return {
        "folds": folds, "oos_metrics": metrics,
        "folds_beating_benchmark": beat, "n_folds": len(folds),
        "equity_curve": [(d.isoformat(), round(float(v), 6)) for d, v in stitched.items()][:: max(1, len(stitched) // 600)],
        "benchmark_curve": [(d.isoformat(), round(float(v), 6)) for d, v in bstitched.items()][:: max(1, len(stitched) // 600)] if bstitched is not None else [],
        "_equity": stitched,
    }


def _stitch(parts: list[pd.Series]) -> pd.Series:
    out = []
    level = 1.0
    for s in parts:
        seg = s * level
        if out:
            seg = seg.iloc[1:]
        out.append(seg)
        level = float(seg.iloc[-1]) if len(seg) else level
    return pd.concat(out)


def _add_years(d: date, n: int) -> date:
    try:
        return d.replace(year=d.year + n)
    except ValueError:
        return d.replace(year=d.year + n, day=28)


def sensitivity(panel: Panel, params: dict, start: date, end: date, benchmark_id: int | None, costs: Costs) -> list[dict]:
    """Robustness: vary costs and portfolio breadth; a strategy whose edge disappears under small changes is fragile."""
    rows = []
    for label, c_mult, tn in [("base", 1, None), ("2x costs", 2, None), ("zero costs", 0, None),
                              ("half breadth", 1, max(2, int(params.get("top_n", 8)) // 2)),
                              ("double breadth", 1, int(params.get("top_n", 8)) * 2)]:
        c = Costs(costs.commission_bps * c_mult, costs.spread_bps * c_mult, costs.slippage_bps * c_mult)
        r = run_backtest(panel, params, start, end, benchmark_id, c, top_n=tn, record_holdings=False)
        rows.append({"scenario": label, **{k: r["metrics"].get(k) for k in ("cagr", "sharpe", "max_drawdown", "excess_cagr", "annual_turnover")}})
    return rows
