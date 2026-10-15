"""Market regime classification from index trend/volatility and macro indicators. Rule-based and transparent."""
from __future__ import annotations

import math
from datetime import timedelta

import numpy as np
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Security
from app.providers.sample import last_business_day
from app.services import market_data as md

REAL_BENCH_CANDIDATES = ["SPY", "VOO", "IVV", "VTI"]
SAMPLE_BENCH = "SMPL500"


def pick_benchmark(db: Session) -> Security | None:
    for sym in REAL_BENCH_CANDIDATES:
        s = db.scalar(select(Security).where(Security.symbol == sym, Security.is_sample.is_(False)))
        if s and md.get_quote(db, s)["price"] is not None:
            return s
    return db.scalar(select(Security).where(Security.symbol == SAMPLE_BENCH, Security.is_sample.is_(True)))


def market_regime(db: Session) -> dict:
    bench = pick_benchmark(db)
    out: dict = {"benchmark": None, "indicators": [], "label": "unknown", "explanation": [], "data_quality": "unavailable"}
    if bench is None:
        out["explanation"].append("No benchmark index with price history is available.")
        return out
    ds, cl = md.close_series(db, bench.id, last_business_day() - timedelta(days=500))
    out["benchmark"] = {"symbol": bench.symbol, "name": bench.name, "is_sample": bench.is_sample}
    out["data_quality"] = "sample" if bench.is_sample else "eod"
    if len(cl) < 210:
        out["explanation"].append("Insufficient benchmark history for regime classification.")
        return out
    arr = np.asarray(cl)
    sma200 = arr[-200:].mean()
    sma50 = arr[-50:].mean()
    rets = np.diff(arr) / arr[:-1]
    vol20 = float(np.std(rets[-20:], ddof=1) * math.sqrt(252))
    vol252 = float(np.std(rets[-252:], ddof=1) * math.sqrt(252))
    dd = float(arr[-1] / arr[-252:].max() - 1)
    trend_up = arr[-1] > sma200 and sma50 > sma200
    stress = vol20 > max(1.5 * vol252, 0.25)
    ind = [
        {"name": "Index vs 200d SMA", "value": float(arr[-1] / sma200 - 1), "fmt": "pct", "source": bench.symbol},
        {"name": "20d realized volatility", "value": vol20, "fmt": "pct", "source": bench.symbol},
        {"name": "1y realized volatility", "value": vol252, "fmt": "pct", "source": bench.symbol},
        {"name": "Drawdown from 52w high", "value": dd, "fmt": "pct", "source": bench.symbol},
    ]
    macro_q = set()
    m10, m2 = md.macro_latest(db, "DGS10"), md.macro_latest(db, "DGS2")
    if m10 and m2:
        ind.append({"name": "Yield curve 10y−2y", "value": (m10["value"] - m2["value"]) / 100, "fmt": "pct",
                    "source": f"{m10['source']} ({m10['date']})", "quality": m10["quality"]})
        macro_q.add(m10["quality"])
    for sid, nm in (("BAMLH0A0HYM2", "High-yield spread"), ("VIXCLS", "Implied volatility index"), ("UNRATE", "Unemployment rate"),
                    ("FEDFUNDS", "Policy rate"), ("CPI_YOY", "CPI inflation YoY")):
        m = md.macro_latest(db, sid)
        if m:
            val = m["value"] / 100 if sid not in ("VIXCLS",) else m["value"]
            ind.append({"name": nm, "value": val, "fmt": "num" if sid == "VIXCLS" else "pct",
                        "source": f"{m['source']} ({m['date']})", "quality": m["quality"]})
            macro_q.add(m["quality"])
    expl = []
    if stress:
        label = "high-volatility stress"
        expl.append(f"Short-term volatility ({vol20:.0%}) is well above its 1-year level ({vol252:.0%}).")
    elif trend_up and dd > -0.10:
        label = "uptrend / risk-on"
        expl.append("Index is above its 200-day average with the 50-day above the 200-day.")
    elif dd <= -0.20:
        label = "bear market"
        expl.append(f"Index is {dd:.0%} below its 52-week high.")
    elif dd <= -0.10:
        label = "correction"
        expl.append(f"Index is {dd:.0%} below its 52-week high.")
    else:
        label = "range-bound / mixed"
        expl.append("Trend signals are mixed.")
    curve = next((i for i in ind if i["name"].startswith("Yield curve")), None)
    if curve and curve["value"] < 0:
        expl.append("Yield curve is inverted (historically associated with elevated recession risk, with long, variable lags).")
    out.update({"label": label, "indicators": ind, "explanation": expl,
                "macro_quality": sorted(macro_q), "as_of": ds[-1].isoformat(),
                "caveat": "Regime labels describe current conditions; they are not forecasts."})
    return out
