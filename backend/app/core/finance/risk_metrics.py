"""Risk statistics on return series. All functions return None when the sample is too small to be meaningful."""
from __future__ import annotations

import math
from collections.abc import Mapping, Sequence

import numpy as np

TRADING_DAYS = 252
MIN_OBS = 20


def _arr(returns: Sequence[float]) -> np.ndarray:
    a = np.asarray([r for r in returns if r is not None and math.isfinite(r)], dtype=float)
    return a


def annualized_volatility(returns: Sequence[float], periods: int = TRADING_DAYS) -> float | None:
    a = _arr(returns)
    if a.size < MIN_OBS:
        return None
    return float(a.std(ddof=1) * math.sqrt(periods))


def annualized_mean(returns: Sequence[float], periods: int = TRADING_DAYS) -> float | None:
    a = _arr(returns)
    if a.size < MIN_OBS:
        return None
    return float((np.prod(1 + a)) ** (periods / a.size) - 1)


def sharpe_ratio(returns: Sequence[float], risk_free_annual: float = 0.0, periods: int = TRADING_DAYS) -> float | None:
    a = _arr(returns)
    if a.size < MIN_OBS:
        return None
    rf = (1 + risk_free_annual) ** (1 / periods) - 1
    ex = a - rf
    sd = ex.std(ddof=1)
    if sd == 0:
        return None
    return float(ex.mean() / sd * math.sqrt(periods))


def sortino_ratio(returns: Sequence[float], risk_free_annual: float = 0.0, periods: int = TRADING_DAYS) -> float | None:
    a = _arr(returns)
    if a.size < MIN_OBS:
        return None
    rf = (1 + risk_free_annual) ** (1 / periods) - 1
    ex = a - rf
    downside = np.minimum(ex, 0)
    dd = math.sqrt((downside**2).mean())
    if dd == 0:
        return None
    return float(ex.mean() / dd * math.sqrt(periods))


def drawdown_series(values: Sequence[float]) -> list[float]:
    peak = -math.inf
    out = []
    for v in values:
        peak = max(peak, v)
        out.append(v / peak - 1.0 if peak > 0 else 0.0)
    return out


def max_drawdown(values: Sequence[float]) -> dict | None:
    """Maximum peak-to-trough decline of a value/price series (negative number) with indices."""
    if len(values) < 2:
        return None
    peak_i = 0
    best = (0.0, 0, 0)
    for i, v in enumerate(values):
        if v > values[peak_i]:
            peak_i = i
        dd = v / values[peak_i] - 1.0 if values[peak_i] > 0 else 0.0
        if dd < best[0]:
            best = (dd, peak_i, i)
    return {"max_drawdown": best[0], "peak_index": best[1], "trough_index": best[2]}


def beta(asset: Sequence[float], market: Sequence[float]) -> float | None:
    a = np.asarray(asset, dtype=float)
    m = np.asarray(market, dtype=float)
    if a.size != m.size or a.size < MIN_OBS:
        return None
    var = m.var(ddof=1)
    if var == 0:
        return None
    return float(np.cov(a, m, ddof=1)[0, 1] / var)


def correlation(a: Sequence[float], b: Sequence[float]) -> float | None:
    x = np.asarray(a, dtype=float)
    y = np.asarray(b, dtype=float)
    if x.size != y.size or x.size < MIN_OBS or x.std() == 0 or y.std() == 0:
        return None
    return float(np.corrcoef(x, y)[0, 1])


def historical_var(returns: Sequence[float], level: float = 0.95) -> dict | None:
    """One-period historical VaR and CVaR (expected shortfall), reported as positive loss fractions."""
    a = _arr(returns)
    if a.size < 60:
        return None
    q = np.quantile(a, 1 - level, method="lower")  # conservative empirical quantile
    tail = a[a <= q]
    return {"var": float(-q), "cvar": float(-tail.mean()) if tail.size else float(-q), "level": level, "n": int(a.size)}


def parametric_var(mu: float, sigma: float, level: float = 0.95) -> float:
    z = {0.90: 1.2816, 0.95: 1.6449, 0.975: 1.96, 0.99: 2.3263}.get(level)
    if z is None:
        raise ValueError("unsupported confidence level")
    return float(-(mu - z * sigma))


def concentration(weights: Mapping[str, float]) -> dict:
    """Herfindahl-Hirschman index and effective number of positions from weights (need not sum to 1)."""
    w = np.asarray([v for v in weights.values() if v > 0], dtype=float)
    if w.size == 0 or w.sum() == 0:
        return {"hhi": None, "effective_n": None, "top_weight": None, "top5_weight": None}
    w = w / w.sum()
    hhi = float((w**2).sum())
    s = np.sort(w)[::-1]
    return {"hhi": hhi, "effective_n": 1 / hhi, "top_weight": float(s[0]), "top5_weight": float(s[:5].sum())}


def portfolio_volatility(weights: Sequence[float], cov_annual: np.ndarray) -> float:
    w = np.asarray(weights, dtype=float)
    return float(math.sqrt(max(w @ cov_annual @ w, 0.0)))


def tracking_error(port: Sequence[float], bench: Sequence[float], periods: int = TRADING_DAYS) -> float | None:
    p = np.asarray(port, dtype=float)
    b = np.asarray(bench, dtype=float)
    if p.size != b.size or p.size < MIN_OBS:
        return None
    return float((p - b).std(ddof=1) * math.sqrt(periods))
