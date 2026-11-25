"""Dashboard datasets for the research story: 2020-24 out-of-sample + 2025-26 timing sensitivity."""
import json, warnings
from pathlib import Path
import pandas as pd
warnings.filterwarnings("ignore")
import backtest as bt
import robust_strategy as rs

HERE = Path(__file__).parent; OUT = HERE / "dashboard"
meta = [{"label": f, "value": (HERE / f).read_text(), "type": "code", "lang": "python"}
        for f in ("robust_strategy.py", "oos_2020.py", "export_v2.py")]
meta.append({"label": "Input", "value": "Twelve Data weekly closes: data/ (2025-05..2026-11), data_2020/ (2019-07..2024-12); approx. distribution yields added (total-return proxy)"})
names = {k: n for k, n, *_ in bt.STRATEGIES}; names["shield_v2"] = "الدرع التكيفي"; names["shield_v1"] = "الدرع التكيفي (نسخة 1)"
r4 = lambda x: None if x is None else round(float(x), 4)

oos = json.load(open(HERE / "oos_2020_results.json"))["tr"]
rows = []
for k, v in oos["table"].items():
    rows.append({"key": k, "name": names.get(k, k), **{c: r4(v[c]) for c in ("total", "cagr", "vol", "sharpe", "maxdd", "off_min", "off_max")},
                 **{f"y{y}": r4(v[str(y)]) for y in range(2020, 2025)}})
rows.sort(key=lambda r: -r["sharpe"])
for i, r in enumerate(rows): r["rank"] = i + 1
navrows = [{"date": d, "key": k, "nav": oos["nav"][k][i]} for k in ("shield_v2", "bh_spus", "stocks_ew") for i, d in enumerate(oos["nav"]["dates"])]

# 2025-26 timing table (total-return proxy), shield v2 added
bt.COST = 0.001
sr = json.load(open(HERE / "shield_results.json"))["tr"]
nav2, turn2 = rs.run_tranches(rs.PX_TR, rs.shield_v2); m2 = bt.metrics(nav2, turn2)
timing = [{"key": k, "name": names.get(k, k), "total": r4(v["total"]), "off_min": r4(v["off_min"]), "off_max": r4(max(v["total"], v["off_mean"] * 2 - v["off_min"]) if False else None),
           "off_mean": r4(v["off_mean"]), "maxdd": r4(v["maxdd"]), "h2": r4(v["h2"])} for k, v in sr.items() if k != "shield"]
# exact best-offset value: recompute
for t in timing:
    fn = next(f for k, n, fam, ev, f, d in bt.STRATEGIES if k == t["key"]); ev = next(ev for k, n, fam, ev, f, d in bt.STRATEGIES if k == t["key"])
    offs = [rs.run_single(rs.PX_TR, fn, ev, offset=o)[0].iloc[-1] - 1 for o in range(min(ev, 4))]
    t["off_max"] = r4(max(offs)); t["off_min"] = r4(min(offs))
h2v2 = rs.run_tranches(rs.PX_TR, rs.shield_v2, start=rs.MID)[0].iloc[-1] - 1
timing.append({"key": "shield_v2", "name": names["shield_v2"], "total": r4(m2["total"]), "off_min": r4(m2["total"]), "off_max": r4(m2["total"]),
               "off_mean": r4(m2["total"]), "maxdd": r4(m2["maxdd"]), "h2": r4(h2v2)})
timing.sort(key=lambda r: -r["off_mean"])
for name, data in (("oos_summary", rows), ("oos_nav", navrows), ("timing", timing)):
    (OUT / f"{name}.json").write_text(json.dumps({"rows": data, "meta": meta}, ensure_ascii=False)); print(name, len(data))
print([ (r["key"], r["total"], r["sharpe"]) for r in rows[:3]], [t for t in timing if t["key"] in ("shield_v2","mom_4_3m","bh_spus")])
