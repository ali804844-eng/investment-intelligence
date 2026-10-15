"""Performance measurement: time-weighted return (TWR), money-weighted return (XIRR), period returns."""
from __future__ import annotations

import math
from collections.abc import Sequence
from datetime import date


def twr_from_valuations(values: Sequence[float], flows: Sequence[float]) -> float | None:
    """Daily-linked TWR.

    ``values[i]`` is the end-of-day portfolio value on day i (after that day's flows) and ``flows[i]`` the
    external cash flow on day i (+ in, - out). Flows are assumed to occur at the start of the day, so the
    day's return is (V_i - F_i) / V_{i-1} - 1. Days where the starting value is zero are skipped
    (no capital at risk). Returns None if there is no measurable period.
    """
    if len(values) != len(flows):
        raise ValueError("values and flows must have the same length")
    growth = 1.0
    measured = False
    for i in range(1, len(values)):
        start = values[i - 1]
        if start <= 0:
            continue
        r = (values[i] - flows[i]) / start - 1.0
        growth *= 1.0 + r
        measured = True
    return growth - 1.0 if measured else None


def daily_twr_series(values: Sequence[float], flows: Sequence[float]) -> list[float | None]:
    out: list[float | None] = [None]
    for i in range(1, len(values)):
        start = values[i - 1]
        out.append(None if start <= 0 else (values[i] - flows[i]) / start - 1.0)
    return out


def xnpv(rate: float, cashflows: Sequence[tuple[date, float]]) -> float:
    t0 = cashflows[0][0]
    return sum(cf / (1.0 + rate) ** ((d - t0).days / 365.0) for d, cf in cashflows)


def xirr(cashflows: Sequence[tuple[date, float]], guess: float = 0.1) -> float | None:
    """Money-weighted return. Convention: investor perspective, contributions negative, terminal value positive.

    Uses Newton's method with a bisection fallback; returns None if no sign change / no convergence.
    """
    cfs = sorted([(d, float(v)) for d, v in cashflows if v != 0], key=lambda x: x[0])
    if len(cfs) < 2 or not (any(v < 0 for _, v in cfs) and any(v > 0 for _, v in cfs)):
        return None
    if (cfs[-1][0] - cfs[0][0]).days == 0:
        return None
    rate = guess
    for _ in range(100):
        t0 = cfs[0][0]
        f = 0.0
        df = 0.0
        for d, v in cfs:
            t = (d - t0).days / 365.0
            base = 1.0 + rate
            if base <= 0:
                break
            f += v / base**t
            df -= t * v / base ** (t + 1)
        if df == 0 or not math.isfinite(f):
            break
        new = rate - f / df
        if new <= -0.9999:
            new = (rate - 0.9999) / 2
        if abs(new - rate) < 1e-10:
            return new
        rate = new
    # Bisection fallback
    lo, hi = -0.9999, 10.0
    flo, fhi = xnpv(lo, cfs), xnpv(hi, cfs)
    if flo * fhi > 0:
        return None
    for _ in range(300):
        mid = (lo + hi) / 2
        fm = xnpv(mid, cfs)
        if abs(fm) < 1e-9:
            return mid
        if flo * fm < 0:
            hi, fhi = mid, fm
        else:
            lo, flo = mid, fm
    return (lo + hi) / 2


def annualize(total_return: float, days: int) -> float | None:
    if days <= 0 or total_return <= -1:
        return None
    return (1 + total_return) ** (365.0 / days) - 1


def simple_returns(prices: Sequence[float]) -> list[float]:
    return [prices[i] / prices[i - 1] - 1.0 for i in range(1, len(prices)) if prices[i - 1] > 0]


def period_return(dates: Sequence[date], values: Sequence[float], start: date) -> float | None:
    """Price return from the last observation on/before ``start`` to the last observation."""
    base = None
    for d, v in zip(dates, values, strict=True):
        if d <= start:
            base = v
        else:
            break
    if base is None or base <= 0 or not values:
        return None
    return values[-1] / base - 1.0
