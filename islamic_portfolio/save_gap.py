import datetime as dt
from pathlib import Path
def save(t, vals, end="2025-04-28", start="2025-01-06"):
    v = vals.split(); e = dt.date.fromisoformat(end); n = (e - dt.date.fromisoformat(start)).days // 7 + 1
    assert len(v) == n, (t, len(v), n)
    rows = [f"{(e - dt.timedelta(weeks=i)).isoformat()},{x}" for i, x in enumerate(v)][::-1]
    Path(__file__).with_name("data_gap").joinpath(f"{t}.csv").write_text("date,close\n" + "\n".join(rows)); print(t, n)
