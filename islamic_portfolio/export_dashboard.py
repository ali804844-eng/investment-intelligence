"""Turn results.json into the dashboard datasets ({rows, meta} JSON files)."""
import json
from pathlib import Path

import pandas as pd

HERE = Path(__file__).parent
OUT = HERE / "dashboard"
OUT.mkdir(exist_ok=True)
d = json.loads((HERE / "results.json").read_text())
code = (HERE / "backtest.py").read_text()
own = Path(__file__).read_text()
meta = [
    {"label": "backtest.py", "value": code, "type": "code", "lang": "python"},
    {"label": "export_dashboard.py", "value": own, "type": "code", "lang": "python"},
    {"label": "Input", "value": "Weekly closes from Twelve Data, 2025-05-05 → 2026-11-16 (data/*.csv)"},
    {"label": "Test window", "value": f"{d['period']['start']} → {d['period']['end']} ({d['period']['weeks']} weeks), cost {d['period']['cost_bps']:.0f} bps per unit turnover"},
]
from backtest import UNIVERSE  # noqa: E402

r4 = lambda x: None if x is None else round(float(x), 4)
strat = []
for s in d["strategies"]:
    strat.append({k: (r4(s[k]) if isinstance(s[k], float) else s[k]) for k in
                  ["rank", "key", "name", "family", "every", "desc", "total", "ann", "vol", "sharpe", "sortino",
                   "maxdd", "calmar", "win", "turnover", "beta", "alpha", "score"]})
nav = []
for k in d["dd"]:
    for i, dt in enumerate(d["nav"]["dates"]):
        nav.append({"date": dt, "key": k, "nav": d["nav"][k][i], "dd": d["dd"][k][i]})
win = d["strategies"][0]
hold = [{"ticker": t, "name": UNIVERSE[t][0], "type": UNIVERSE[t][1], "weight": round(w, 4)}
        for t, w in sorted(win["weights"].items(), key=lambda x: -x[1])]
assets = [{k: (r4(a[k]) if isinstance(a[k], float) else a[k]) for k in ["ticker", "name", "type", "sector", "ret", "vol", "maxdd"]}
          for a in d["assets"]]
grid = [{**g, "total": r4(g["total"]), "sharpe": r4(g["sharpe"]), "maxdd": r4(g["maxdd"])} for g in d["robust"]["grid"]]
costs = [{"bps": int(c["bps"]), "total": r4(c["total"])} for c in d["robust"]["costs"]]
px = []
for t in UNIVERSE:
    s = pd.read_csv(HERE / "data" / f"{t}.csv").sort_values("date")
    for _, row in s.iterrows():
        px.append({"date": row["date"], "ticker": t, "close": float(row["close"])})
for name, rows in [("strategies", strat), ("nav", nav), ("holdings", hold), ("assets", assets),
                   ("grid", grid), ("costs", costs), ("prices", px)]:
    (OUT / f"{name}.json").write_text(json.dumps({"rows": rows, "meta": meta}, ensure_ascii=False))
    print(name, len(rows))
print(hold)
