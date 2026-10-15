"""Short-term (swing/position) setup detection with explicit hypotheses, entry/invalidation rules, risk/reward
and a historical event study on the same instrument.

Historical statistics describe what happened after past occurrences of the same pattern on the same
instrument. They are descriptive, not evidence of a forward-looking edge: sample sizes are small, the
pattern definitions were chosen in advance but not out-of-sample validated, and regimes change.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from app.core.finance import indicators as ind


@dataclass
class OHLCV:
    dates: list
    open: list[float]
    high: list[float]
    low: list[float]
    close: list[float]
    volume: list[float]


SETUPS = {
    "breakout": {
        "title": "55-day breakout with volume",
        "hypothesis": "A close at a multi-month high on above-average volume can mark the start of a continued advance "
                      "(trend-following / time-series momentum).",
        "holding_days": 20, "stop_atr": 2.0, "target_r": 2.0,
    },
    "pullback": {
        "title": "Pullback within an uptrend",
        "hypothesis": "In an established uptrend (50d > 200d SMA), a short-term pullback toward the 50-day average may "
                      "offer a lower-risk entry if the trend resumes.",
        "holding_days": 15, "stop_atr": 1.5, "target_r": 2.0,
    },
    "oversold_reversal": {
        "title": "Oversold reversal above the 200-day average",
        "hypothesis": "Short-term oversold conditions (RSI<30) in a long-term uptrend have historically tended to mean-revert "
                      "on some instruments.",
        "holding_days": 10, "stop_atr": 2.0, "target_r": 1.5,
    },
}


def _signals(o: OHLCV) -> dict[str, list[bool]]:
    c, h, v = o.close, o.high, o.volume
    n = len(c)
    sma50, sma200 = ind.sma(c, 50), ind.sma(c, 200)
    rsi = ind.rsi(c, 14)
    vavg = ind.sma(v, 20)
    prior_high = [None] + ind.rolling_high(h, 55)[:-1]
    out = {k: [False] * n for k in SETUPS}
    for i in range(n):
        if sma200[i] is None or sma50[i] is None:
            continue
        up = c[i] > sma200[i]
        if prior_high[i] is not None and vavg[i] and c[i] > prior_high[i] and v[i] >= 1.5 * vavg[i] and up:
            out["breakout"][i] = True
        if sma50[i] > sma200[i] and up and rsi[i] is not None and rsi[i] < 40 and abs(c[i] / sma50[i] - 1) <= 0.03:
            out["pullback"][i] = True
        if up and rsi[i] is not None and rsi[i] < 30:
            out["oversold_reversal"][i] = True
    return out


def _simulate(o: OHLCV, i: int, stop: float, target: float, max_days: int, cost_frac: float) -> dict | None:
    """Enter at next day's open; exit at stop/target intraday (stop checked first, conservative) or time exit."""
    if i + 1 >= len(o.close):
        return None
    entry = o.open[i + 1] or o.close[i]
    risk = entry - stop
    if risk <= 0:
        return None
    for j in range(i + 1, min(i + 1 + max_days, len(o.close))):
        if o.low[j] <= stop:
            exit_px = min(stop, o.open[j] or stop)
            return _trade(entry, exit_px, risk, j - i, "stop", cost_frac)
        if o.high[j] >= target:
            exit_px = max(target, o.open[j] or target)
            return _trade(entry, exit_px, risk, j - i, "target", cost_frac)
    j = min(i + max_days, len(o.close) - 1)
    if j <= i:
        return None
    return _trade(entry, o.close[j], risk, j - i, "time", cost_frac)


def _trade(entry, exit_px, risk, days, reason, cost_frac):
    gross = exit_px / entry - 1
    net = gross - 2 * cost_frac
    return {"return": net, "r_multiple": (exit_px - entry - 2 * cost_frac * entry) / risk, "days": days, "exit": reason}


def detect(o: OHLCV, costs_bps: float = 15.0, events: list[dict] | None = None) -> list[dict]:
    """Return active setups on the latest bar with full trade plans and historical stats."""
    n = len(o.close)
    if n < 260:
        return []
    sig = _signals(o)
    atr = ind.atr(o.high, o.low, o.close, 14)
    cost_frac = costs_bps / 10_000
    last = n - 1
    out = []
    for key, spec in SETUPS.items():
        if not sig[key][last]:
            continue
        a = atr[last]
        if a is None or a <= 0:
            continue
        entry = o.close[last]
        stop = entry - spec["stop_atr"] * a
        target = entry + spec["target_r"] * (entry - stop)
        # historical event study (non-overlapping occurrences, excluding the current one)
        trades = []
        i = 210
        while i < last - spec["holding_days"]:
            if sig[key][i] and atr[i]:
                st = o.close[i] - spec["stop_atr"] * atr[i]
                tg = o.close[i] + spec["target_r"] * (o.close[i] - st)
                t = _simulate(o, i, st, tg, spec["holding_days"], cost_frac)
                if t:
                    trades.append(t)
                    i += spec["holding_days"]
                    continue
            i += 1
        stats = _stats(trades)
        earn = [e for e in (events or []) if e.get("kind") == "earnings" and e.get("days_until") is not None
                and 0 <= e["days_until"] <= spec["holding_days"] * 1.5]
        out.append({
            "setup": key, "title": spec["title"], "hypothesis": spec["hypothesis"],
            "as_of": str(o.dates[last]),
            "entry_conditions": _entry_text(key),
            "entry_reference": entry,
            "invalidation": {"stop": stop, "rule": f"Close below {stop:.2f} ({spec['stop_atr']}×ATR(14) below entry) "
                                                    "invalidates the setup"},
            "exits": [{"type": "target", "price": target, "rule": f"{spec['target_r']}R profit target"},
                      {"type": "time", "rule": f"Exit after {spec['holding_days']} trading days if neither level is hit"}],
            "holding_period_days": spec["holding_days"],
            "risk_reward": {"risk_per_share": entry - stop, "reward_per_share": target - entry,
                            "ratio": (target - entry) / (entry - stop),
                            "breakeven_win_rate": 1 / (1 + (target - entry) / (entry - stop))},
            "cost_assumptions": {"round_trip_bps": 2 * costs_bps,
                                 "note": "Spread + slippage + commission per side; gaps through stops can cause larger losses."},
            "atr14": a,
            "event_risk": [f"Earnings in {e['days_until']} days (inside holding window)" for e in earn],
            "historical": stats,
            "evidence_label": _evidence_label(stats),
            "caveat": "Historical tendency on this instrument only — not a reliable forward-looking edge.",
        })
    return out


def _entry_text(key: str) -> list[str]:
    return {
        "breakout": ["Close above the prior 55-day high", "Volume ≥ 1.5× 20-day average", "Close above 200-day SMA",
                     "Entry: next session, only if price holds above the breakout level"],
        "pullback": ["50-day SMA above 200-day SMA (uptrend)", "RSI(14) below 40", "Close within 3% of the 50-day SMA",
                     "Entry: on a close back above the prior day's high (confirmation)"],
        "oversold_reversal": ["Close above 200-day SMA", "RSI(14) below 30",
                              "Entry: next session open; avoid if a major event is scheduled"],
    }[key]


def _stats(trades: list[dict]) -> dict:
    if not trades:
        return {"n": 0}
    r = np.array([t["return"] for t in trades])
    rm = np.array([t["r_multiple"] for t in trades])
    return {"n": len(trades), "win_rate": float((r > 0).mean()), "avg_return": float(r.mean()),
            "median_return": float(np.median(r)), "avg_r_multiple": float(rm.mean()),
            "worst_return": float(r.min()), "best_return": float(r.max()),
            "avg_days_held": float(np.mean([t["days"] for t in trades])),
            "exit_mix": {k: sum(1 for t in trades if t["exit"] == k) for k in ("stop", "target", "time")},
            "t_stat": float(r.mean() / (r.std(ddof=1) / math.sqrt(len(r)))) if len(r) > 2 and r.std(ddof=1) > 0 else None}


def _evidence_label(stats: dict) -> str:
    n = stats.get("n", 0)
    if n < 10:
        return "insufficient history (fewer than 10 past occurrences)"
    t = stats.get("t_stat")
    if n < 30:
        return "weak: small sample"
    if t is not None and t > 2:
        return "historically positive on this instrument (in-sample; not validated out-of-sample)"
    return "no statistically meaningful historical tendency"


def technical_snapshot(o: OHLCV) -> dict:
    c = o.close
    if len(c) < 30:
        return {}
    sma20, sma50, sma200 = ind.sma(c, 20), ind.sma(c, 50), ind.sma(c, 200)
    m = ind.macd(c)
    bb = ind.bollinger(c)
    sr = ind.support_resistance(o.high[-260:], o.low[-260:])
    atr = ind.atr(o.high, o.low, o.close)
    return {"close": c[-1], "sma20": sma20[-1], "sma50": sma50[-1], "sma200": sma200[-1], "rsi14": ind.rsi(c)[-1],
            "macd": m["macd"][-1], "macd_signal": m["signal"][-1], "bb_upper": bb["upper"][-1], "bb_lower": bb["lower"][-1],
            "atr14": atr[-1], "support_resistance": sr,
            "support_resistance_note": "Levels are swing-pivot hypotheses, not guaranteed reaction points."}
