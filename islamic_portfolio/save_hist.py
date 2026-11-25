"""Save newest-first weekly closes typed from Twelve Data output; checks week count against the date span."""
import sys, datetime as dt
from pathlib import Path
def save(t, end, start, vals):
    v = vals.split(); e = dt.date.fromisoformat(end); s = dt.date.fromisoformat(start)
    n = (e - s).days // 7 + 1
    assert len(v) == n, f"{t}: got {len(v)} values, expected {n}"
    rows = [f"{(e - dt.timedelta(weeks=i)).isoformat()},{x}" for i, x in enumerate(v)][::-1]
    Path(__file__).with_name("data_2020").joinpath(f"{t}.csv").write_text("date,close\n" + "\n".join(rows))
    print(t, n, "ok")
