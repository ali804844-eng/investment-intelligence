"""Technical indicators. Each returns a list aligned to the input with None during warm-up.
Indicators only use data up to and including index i (no look-ahead)."""
from __future__ import annotations

import math
from collections.abc import Sequence


def sma(x: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    s = 0.0
    for i, v in enumerate(x):
        s += v
        if i >= n:
            s -= x[i - n]
        out.append(s / n if i >= n - 1 else None)
    return out


def ema(x: Sequence[float], n: int) -> list[float | None]:
    out: list[float | None] = []
    k = 2 / (n + 1)
    prev = None
    for i, v in enumerate(x):
        if i < n - 1:
            out.append(None)
            continue
        if prev is None:
            prev = sum(x[i - n + 1 : i + 1]) / n  # seed with SMA
        else:
            prev = v * k + prev * (1 - k)
        out.append(prev)
    return out


def rsi(x: Sequence[float], n: int = 14) -> list[float | None]:
    """Wilder's RSI."""
    out: list[float | None] = [None] * len(x)
    if len(x) <= n:
        return out
    gains = losses = 0.0
    for i in range(1, n + 1):
        d = x[i] - x[i - 1]
        gains += max(d, 0)
        losses += max(-d, 0)
    ag, al = gains / n, losses / n
    out[n] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(x)):
        d = x[i] - x[i - 1]
        ag = (ag * (n - 1) + max(d, 0)) / n
        al = (al * (n - 1) + max(-d, 0)) / n
        out[i] = 100.0 if al == 0 else 100 - 100 / (1 + ag / al)
    return out


def macd(x: Sequence[float], fast: int = 12, slow: int = 26, signal: int = 9) -> dict[str, list[float | None]]:
    ef, es = ema(x, fast), ema(x, slow)
    line = [(a - b) if (a is not None and b is not None) else None for a, b in zip(ef, es, strict=True)]
    valid = [v for v in line if v is not None]
    sig_valid = ema(valid, signal) if valid else []
    sig: list[float | None] = [None] * (len(line) - len(valid)) + sig_valid
    hist = [(a - b) if (a is not None and b is not None) else None for a, b in zip(line, sig, strict=True)]
    return {"macd": line, "signal": sig, "hist": hist}


def true_range(high: Sequence[float], low: Sequence[float], close: Sequence[float]) -> list[float]:
    tr = [high[0] - low[0]]
    for i in range(1, len(close)):
        tr.append(max(high[i] - low[i], abs(high[i] - close[i - 1]), abs(low[i] - close[i - 1])))
    return tr


def atr(high: Sequence[float], low: Sequence[float], close: Sequence[float], n: int = 14) -> list[float | None]:
    tr = true_range(high, low, close)
    out: list[float | None] = [None] * len(tr)
    if len(tr) < n:
        return out
    prev = sum(tr[:n]) / n
    out[n - 1] = prev
    for i in range(n, len(tr)):
        prev = (prev * (n - 1) + tr[i]) / n
        out[i] = prev
    return out


def bollinger(x: Sequence[float], n: int = 20, k: float = 2.0) -> dict[str, list[float | None]]:
    mid = sma(x, n)
    up: list[float | None] = []
    lo: list[float | None] = []
    for i, m in enumerate(mid):
        if m is None:
            up.append(None)
            lo.append(None)
            continue
        w = x[i - n + 1 : i + 1]
        sd = math.sqrt(sum((v - m) ** 2 for v in w) / n)
        up.append(m + k * sd)
        lo.append(m - k * sd)
    return {"mid": mid, "upper": up, "lower": lo}


def momentum(x: Sequence[float], lookback: int, skip: int = 0) -> list[float | None]:
    """Total return from t-lookback to t-skip (e.g. 12-1 momentum: lookback=252, skip=21)."""
    out: list[float | None] = []
    for i in range(len(x)):
        a, b = i - lookback, i - skip
        out.append(x[b] / x[a] - 1 if a >= 0 and x[a] > 0 else None)
    return out


def rolling_high(x: Sequence[float], n: int) -> list[float | None]:
    return [max(x[i - n + 1 : i + 1]) if i >= n - 1 else None for i in range(len(x))]


def rolling_low(x: Sequence[float], n: int) -> list[float | None]:
    return [min(x[i - n + 1 : i + 1]) if i >= n - 1 else None for i in range(len(x))]


def support_resistance(high: Sequence[float], low: Sequence[float], window: int = 5, max_levels: int = 4) -> dict:
    """Swing-pivot based support/resistance *hypotheses* (local extrema), clustered within 1.5%."""
    piv_hi, piv_lo = [], []
    for i in range(window, len(high) - window):
        if high[i] == max(high[i - window : i + window + 1]):
            piv_hi.append(high[i])
        if low[i] == min(low[i - window : i + window + 1]):
            piv_lo.append(low[i])

    def cluster(levels: list[float]) -> list[dict]:
        levels = sorted(levels)
        groups: list[list[float]] = []
        for lv in levels:
            if groups and abs(lv / groups[-1][-1] - 1) < 0.015:
                groups[-1].append(lv)
            else:
                groups.append([lv])
        res = [{"level": sum(g) / len(g), "touches": len(g)} for g in groups]
        return sorted(res, key=lambda r: -r["touches"])[:max_levels]

    return {"resistance": cluster(piv_hi), "support": cluster(piv_lo)}
