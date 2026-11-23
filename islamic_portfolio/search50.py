"""Wide parameter search for strategies above +50% over the last 52 weeks,
plus a walk-forward check (choose on first 26 weeks, test on last 26)."""
import itertools, json
import numpy as np, pandas as pd
import backtest as bt
from backtest import load_prices, metrics, UNIVERSE

px = load_prices(); px = px.loc[px["SPUS"].notna()]
rets = px.pct_change(fill_method=None)
STOCKS = [t for t in UNIVERSE if UNIVERSE[t][1] == "سهم"]

def mom(lb, top, universe, score="ret", absf=True):
    def f(h):
        c = [t for t in universe if t in h and pd.notna(h[t].iloc[-1]) and pd.notna(h[t].iloc[-1 - lb])]
        m = h[c].iloc[-1] / h[c].iloc[-1 - lb] - 1
        if score == "riskadj":
            v = h[c].pct_change().iloc[-lb:].std().replace(0, np.nan)
            m = m / v
        best = m.sort_values(ascending=False).head(top)
        if absf:
            best = best[(h[best.index].iloc[-1] / h[best.index].iloc[-1 - lb] - 1) > 0]
        if best.empty: return pd.Series({"SPSK": 1.0})
        return pd.Series(1 / len(best), index=best.index)
    return f

def run_window(fn, every, start, end, offset=0):
    """start/end: index positions; signal at t, return t+1."""
    nav=[1.0]; w=pd.Series(0.0,index=px.columns); turn=0
    for i,t in enumerate(range(start, end)):
        if (i - offset) % every == 0 or i == 0:
            new = fn(px.iloc[:t+1]).reindex(px.columns).fillna(0.0)
            tr = (new-w).abs().sum(); turn += tr; w = new; cost = bt.COST*tr
        else: cost = 0
        r = rets.iloc[t+1].fillna(0.0)
        nav.append(nav[-1]*(1+float((w*r).sum())-cost))
        g = w*(1+r); w = g/g.sum()
    return pd.Series(nav, index=px.index[start:end+1]), turn

N = len(px); S = N-53; E = N-1; MID = S+26
univs = {"all": list(UNIVERSE), "stocks": STOCKS}
rows=[]
for (un, U), lb, top, every, score, absf in itertools.product(univs.items(), (1,2,3,4,6,8,12), (1,2,3), (1,2,4), ("ret","riskadj"), (True,False)):
    fn = mom(lb, top, U, score, absf)
    nav, turn = run_window(fn, every, S, E)
    m = metrics(nav, turn)
    h1,_ = run_window(fn, every, S, MID); h2,_ = run_window(fn, every, MID, E)
    rows.append(dict(univ=un, lb=lb, top=top, every=every, score=score, absf=absf,
                     total=m["total"], sharpe=m["sharpe"], maxdd=m["maxdd"], vol=m["vol"], turnover=turn,
                     h1=h1.iloc[-1]-1, h2=h2.iloc[-1]-1))
df = pd.DataFrame(rows)
print("configs:", len(df), " >50%:", (df.total>0.5).sum(), " median:", round(df.total.median(),3))
pd.set_option("display.width",220)
print(df.sort_values("total",ascending=False).head(15).round(3).to_string())

# walk-forward: choose best config on H1, see what it did on H2
best_h1 = df.sort_values("h1",ascending=False).head(10)
print("\nTop-10 picked on first half -> second-half result:")
print(best_h1[["univ","lb","top","every","score","absf","h1","h2","total"]].round(3).to_string())
print("median H2 of all configs:", round(df.h2.median(),3))
# SPUS halves
for a,b in [(S,MID),(MID,E)]:
    print("SPUS", round(px.SPUS.iloc[b]/px.SPUS.iloc[a]-1,3))
# offset sensitivity for the top config
t = df.sort_values("total",ascending=False).iloc[0]
fn = mom(int(t.lb), int(t.top), univs[t.univ], t.score, bool(t.absf))
offs=[round(run_window(fn,int(t.every),S,E,o)[0].iloc[-1]-1,3) for o in range(int(t.every))]
print("rebalance-day offsets for top config:", offs)
df.to_json("search50.json", orient="records", force_ascii=False)
