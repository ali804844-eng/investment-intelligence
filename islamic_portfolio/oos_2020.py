"""Out-of-sample test 2020-2024 of the Adaptive Shield (rules frozen as designed on 2025-26 data)
and of all 20 original strategies. AMANX excluded (unadjustable annual capital-gain distributions)."""
import json, warnings
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
import backtest as bt
from backtest import STRATEGIES, metrics
import robust_strategy as rs

frames = {}
for t in rs.YIELD:
    try:
        s = pd.read_csv(f"data_2020/{t}.csv", parse_dates=["date"]).set_index("date")["close"]
        frames[t] = s
    except FileNotFoundError:
        pass
PX = pd.DataFrame(frames).sort_index()
PX_TR = rs.total_return(PX)
S = PX.index.get_loc(pd.Timestamp("2020-01-06")); E = len(PX) - 1
bt.COST = 0.001

def run(px, key, fn, every, start=S, end=E):
    if key.startswith("shield"):
        return rs.run_tranches(px, fn, start=start, end=end)
    return rs.run_single(px, fn, every, start=start, end=end)

def evaluate(px):
    cands = [(k, n, every, fn) for k, n, f, every, fn, d in STRATEGIES]
    cands += [("shield_v1", "الدرع التكيفي (نسخة 1)", 4, rs.adaptive_shield), ("shield_v2", "الدرع التكيفي (نسخة 2)", 4, rs.shield_v2)]
    rows, navs = [], {}
    years = sorted(set(PX.index[S:].year))
    for key, name, every, fn in cands:
        try:
            nav, turn = run(px, key, fn, every)
        except Exception as e:
            print("skip", key, e); continue
        m = metrics(nav, turn)
        yr = {}
        for y in years:
            sub = nav[nav.index.year == y]
            prev = nav[nav.index.year < y]
            base = prev.iloc[-1] if len(prev) else sub.iloc[0]
            yr[str(y)] = sub.iloc[-1] / base - 1
        offs = [nav.iloc[-1] - 1]
        if not key.startswith("shield") and every > 1:
            offs = [rs.run_single(px, fn, every, start=S, end=E, offset=o)[0].iloc[-1] - 1 for o in range(min(every, 4))]
        rows.append(dict(key=key, name=name, total=m["total"], cagr=m["ann"], vol=m["vol"], sharpe=m["sharpe"],
                         maxdd=m["maxdd"], calmar=m["calmar"], turnover=turn / (len(nav) / 52), off_min=min(offs),
                         off_max=max(offs), **yr))
        navs[key] = nav
    return pd.DataFrame(rows).set_index("key"), navs

if __name__ == "__main__":
    pd.set_option("display.width", 250)
    out = {}
    for lab, px in (("price", PX), ("tr", PX_TR)):
        df, navs = evaluate(px)
        df = df.sort_values("sharpe", ascending=False)
        pct = ["total", "cagr", "vol", "maxdd", "off_min", "off_max"] + [c for c in df.columns if c.isdigit()]
        show = df.copy(); show[pct] = (show[pct] * 100).round(1); show[["sharpe", "calmar", "turnover"]] = show[["sharpe", "calmar", "turnover"]].round(2)
        print(f"\n===== 2020-01 .. 2024-12 ({lab}) =====")
        print(show.drop(columns="name").to_string())
        out[lab] = dict(table=json.loads(df.to_json(orient="index")),
                        nav={"dates": [str(d.date()) for d in navs["bh_spus"].index],
                             **{k: [round(float(x) * 100, 2) for x in v] for k, v in navs.items()}})
    # crisis windows (TR)
    nav = {k: v for k, v in zip(out["tr"]["nav"].keys(), out["tr"]["nav"].values())}
    d = pd.to_datetime(out["tr"]["nav"]["dates"])
    print("\n===== Crisis windows (TR) =====")
    for nm, a, b in [("COVID crash", "2020-02-17", "2020-03-23"), ("2022 bear", "2021-12-27", "2022-10-10"), ("Aug-Oct 2023 dip", "2023-07-24", "2023-10-23")]:
        ia, ib = d.get_loc(pd.Timestamp(a)), d.get_loc(pd.Timestamp(b))
        print(nm, {k: f"{out['tr']['nav'][k][ib]/out['tr']['nav'][k][ia]-1:+.1%}" for k in ("shield_v2", "shield_v1", "bh_spus", "stocks_ew", "mom_4_3m", "bal_60_40", "trend")})
    json.dump(out, open("oos_2020_results.json", "w"), ensure_ascii=False)
