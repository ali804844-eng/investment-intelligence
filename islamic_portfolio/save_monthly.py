"""Save newest-first monthly adjusted closes (Alpha Vantage, incl. distributions), ending 2026-11 (partial month)."""
from pathlib import Path
import pandas as pd
def save(t, vals, end="2026-11"):
    v = [float(x) for x in vals.split()]
    idx = pd.period_range(end=end, periods=len(v), freq="M")[::-1]
    s = pd.Series(v, index=idx.astype(str)).iloc[::-1]
    Path(__file__).with_name("funds").joinpath(f"{t}.csv").write_text("month,adj_close\n" + "\n".join(f"{k},{x}" for k, x in s.items()))
    print(t, len(v), s.index[0], "->", s.index[-1])
