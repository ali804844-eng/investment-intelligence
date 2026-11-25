"""Lessons from 20 strategies -> one strategy designed to avoid their mistakes.

Design rules were fixed from the diagnostics BEFORE running the new strategy;
parameters are round, a-priori choices (not tuned). Validation: full year,
both halves, every rebalance offset, every end date in the last 8 weeks,
parameter neighbourhood, with price-only and approx total-return data.
"""
import json
import numpy as np, pandas as pd
import backtest as bt
from backtest import load_prices, UNIVERSE, STRATEGIES, metrics

# ---- approximate annual distribution yields (public fund/stock yields, rounded) -> total-return proxy
YIELD = {"SPUS": .006, "HLAL": .007, "UMMA": .010, "AMANX": .003, "SPSK": .045, "SPRE": .035,
         "AAPL": .004, "MSFT": .007, "NVDA": .0003, "TSM": .012, "LLY": .006, "JNJ": .025, "XOM": .034}

PX = load_prices(); PX = PX.loc[PX["SPUS"].notna()]
def total_return(px):
    steps = pd.Series(YIELD).reindex(px.columns).fillna(0).values / 52
    return px * (1 + steps)[None, :] ** np.arange(len(px))[:, None]
PX_TR = total_return(PX)
N = len(PX); S = N - 53; E = N - 1; MID = S + 26
RISK = [t for t in UNIVERSE if t != "SPSK"]

# ---- the new strategy: "Adaptive Shield" ------------------------------------------
P = dict(lookbacks=(4, 8, 13, 26), keep=0.5, sma=20, cap=0.20, vol_win=13,
         breadth_half=0.5, breadth_quarter=0.25, target_vol=0.15)

def adaptive_shield(h, p=P):
    last = h.iloc[-1]
    cand = [t for t in RISK if pd.notna(last.get(t))]
    # (1) ensemble momentum: average rank over several look-backs (fixes single-parameter luck)
    ranks = []
    for lb in p["lookbacks"]:
        if len(h) > lb:
            m = (h[cand].iloc[-1] / h[cand].iloc[-1 - lb] - 1)
            ranks.append(m.rank(pct=True))
    score = pd.concat(ranks, axis=1).mean(axis=1).dropna()
    # (2) per-asset trend filter (fixes buying falling knives / momentum reversals)
    sma = h[cand].iloc[-p["sma"]:].mean()
    up = [t for t in score.index if last[t] > sma[t]]
    breadth = len(up) / len(cand)
    chosen = score[up].sort_values(ascending=False)
    chosen = chosen.head(max(1, int(round(len(cand) * p["keep"])))) if len(chosen) else chosen
    w = pd.Series(dtype=float)
    if len(chosen):
        # (3) inverse-volatility weights with a cap (fixes concentration in 1-3 names)
        vol = h[chosen.index].pct_change().iloc[-p["vol_win"]:].std().replace(0, np.nan)
        w = (1 / vol).dropna(); w = w / w.sum()
        for _ in range(10):
            w = w.clip(upper=p["cap"]); w = w / w.sum() if w.sum() > 1 else w
        w = w.clip(upper=p["cap"])
        # (4) graded exposure from market breadth, not a binary switch (fixes SMA whipsaw)
        expo = 1.0 if breadth >= p["breadth_half"] else 0.5 if breadth >= p["breadth_quarter"] else 0.25
        # (5) volatility targeting at portfolio level using the full covariance (fixes correlation spikes)
        r = h[w.index].pct_change().iloc[-8:]
        pv = float(np.sqrt(w.values @ r.cov().values @ w.values) * np.sqrt(52)) if len(w) > 1 else float(r.std().iloc[0] * np.sqrt(52))
        expo = min(expo, p["target_vol"] / pv) if pv > 0 else expo
        w = w * expo / w.sum() * min(1.0, w.sum())
    rest = 1 - w.sum()
    # (6) safe asset only if it is itself in an up-trend, otherwise cash (fixes sukuk drag)
    if rest > 1e-9:
        s = h["SPSK"]
        if s.iloc[-1] > s.iloc[-p["sma"]:].mean():
            w["SPSK"] = w.get("SPSK", 0) + rest
    return w

# ---- tranche runner (fixes rebalance-date luck & cuts turnover) ---------------------
def run_tranches(px, fn, every=4, start=S, end=E, band=0.02):
    """`every` sleeves of equal capital, each rebalanced every `every` weeks on a different week.
    A sleeve trades only when an asset is off target by more than `band` (no-trade band)."""
    rets = px.pct_change(fill_method=None).fillna(0.0)
    cols = px.columns
    sleeves = [pd.Series(0.0, index=cols) for _ in range(every)]
    vals = [1.0 / every] * every
    nav, turn = [1.0], 0.0
    for i, t in enumerate(range(start, end)):
        due = range(every) if i == 0 else [i % every]
        tgt = fn(px.iloc[: t + 1]).reindex(cols).fillna(0.0) if due else None
        for k in due:
            if i == 0 or (tgt - sleeves[k]).abs().max() > band:
                tr = (tgt - sleeves[k]).abs().sum(); turn += tr / every
                vals[k] *= (1 - bt.COST * tr); sleeves[k] = tgt.copy()
        r = rets.iloc[t + 1]
        for j in range(every):
            w = sleeves[j]
            growth = float((w * (1 + r)).sum() + (1 - w.sum()))
            vals[j] *= growth
            sleeves[j] = w * (1 + r) / growth
        nav.append(sum(vals))
    return pd.Series(nav, index=px.index[start:end + 1]), turn


def run_single(px, fn, every, start=S, end=E, offset=0):
    rets = px.pct_change(fill_method=None).fillna(0.0)
    w = pd.Series(0.0, index=px.columns); nav = [1.0]; turn = 0.0
    for i, t in enumerate(range(start, end)):
        cost = 0.0
        if i == 0 or (i - offset) % every == 0:
            new = fn(px.iloc[: t + 1]).reindex(px.columns).fillna(0.0)
            tr = (new - w).abs().sum(); turn += tr; cost = bt.COST * tr; w = new
        r = rets.iloc[t + 1]
        growth = float((w * (1 + r)).sum() + (1 - w.sum()))
        nav.append(nav[-1] * (growth - cost))
        w = w * (1 + r) / growth
    return pd.Series(nav, index=px.index[start:end + 1]), turn

def full_report(px, label):
    out = {}
    cands = [(k, n, every, fn) for k, n, f, every, fn, d in STRATEGIES]
    for key, name, every, fn in cands + [("shield", "الدرع التكيفي", 4, adaptive_shield)]:
        if key == "shield":
            nav, turn = run_tranches(px, fn)
            offs = [nav.iloc[-1] - 1] * 4  # tranche design: no offset dependence
            h1 = run_tranches(px, fn, end=MID)[0].iloc[-1] - 1
            h2 = run_tranches(px, fn, start=MID)[0].iloc[-1] - 1
        else:
            nav, turn = run_single(px, fn, every)
            offs = [run_single(px, fn, every, offset=o)[0].iloc[-1] - 1 for o in range(min(every, 4))]
            h1 = run_single(px, fn, every, end=MID)[0].iloc[-1] - 1
            h2 = run_single(px, fn, every, start=MID)[0].iloc[-1] - 1
        m = metrics(nav, turn)
        ends = [nav.iloc[-1 - j] / nav.iloc[0] - 1 for j in range(8)]
        out[key] = dict(name=name, total=m["total"], sharpe=m["sharpe"], maxdd=m["maxdd"], vol=m["vol"],
                        turnover=turn, h1=h1, h2=h2, off_min=min(offs), off_mean=float(np.mean(offs)),
                        end_min=min(ends), end_max=max(ends), nav=nav)
    df = pd.DataFrame({k: {c: v for c, v in d.items() if c != "nav"} for k, d in out.items()}).T
    for c in df.columns:
        if c != "name": df[c] = df[c].astype(float)
    print(f"\n===== {label} =====")
    pd.set_option("display.width", 220)
    show = df.drop(columns="name").sort_values("off_mean", ascending=False)
    print((show[["total", "off_mean", "off_min", "h1", "h2", "maxdd", "vol", "end_min", "end_max"]] * 100).round(1)
          .assign(sharpe=show.sharpe.round(2), turnover=show.turnover.round(1)).to_string())
    return df, out

if __name__ == "__main__":
    bt.COST = 0.001
    dfp, outp = full_report(PX, "PRICE ONLY")
    dft, outt = full_report(PX_TR, "APPROX TOTAL RETURN (with distributions)")
    # parameter neighbourhood of the new strategy (total-return data)
    sens = []
    for name, changes in [("base", {}), ("cap 15%", {"cap": .15}), ("cap 25%", {"cap": .25}),
                          ("keep 40%", {"keep": .4}), ("keep 60%", {"keep": .6}),
                          ("SMA 13", {"sma": 13}), ("SMA 26", {"sma": 26}),
                          ("vol target 12%", {"target_vol": .12}), ("vol target 18%", {"target_vol": .18}),
                          ("lookbacks 4/13", {"lookbacks": (4, 13)}), ("lookbacks 8/26", {"lookbacks": (8, 26)}),
                          ("no breadth rule", {"breadth_half": 0, "breadth_quarter": 0}),
                          ("no vol target", {"target_vol": 9.9})]:
        p = {**P, **changes}
        nav, turn = run_tranches(PX_TR, lambda h, p=p: adaptive_shield(h, p))
        m = metrics(nav, turn)
        sens.append(dict(variant=name, total=round(m["total"], 4), sharpe=round(m["sharpe"], 3), maxdd=round(m["maxdd"], 4)))
    print("\n===== Shield parameter neighbourhood (total-return) =====")
    print(pd.DataFrame(sens).to_string(index=False))
    # current target weights
    w = adaptive_shield(PX_TR).sort_values(ascending=False)
    print("\nShield weights for next period:", (w * 100).round(1).to_dict(), " cash:", round((1 - w.sum()) * 100, 1))
    # exposure path
    ex = [(str(PX.index[t].date()), round(float(adaptive_shield(PX_TR.iloc[:t + 1]).drop("SPSK", errors="ignore").sum()), 2)) for t in range(S, E + 1, 4)]
    print("equity exposure path:", ex)
    res = dict(price={k: {c: (v if c == "name" else float(v)) for c, v in d.items() if c != "nav"} for k, d in outp.items()},
               tr={k: {c: (v if c == "name" else float(v)) for c, v in d.items() if c != "nav"} for k, d in outt.items()},
               sens=sens, weights={k: float(v) for k, v in w.items()}, exposure=ex,
               nav={"dates": [str(d.date()) for d in outt["shield"]["nav"].index],
                    **{k: [round(float(x) * 100, 2) for x in outt[k]["nav"]] for k in ("shield", "bh_spus", "mom_4_3m", "stocks_ew")}})
    json.dump(res, open("shield_results.json", "w"), ensure_ascii=False)


# ---- version 2: drop the momentum selection (IC ~ 0 = noise); keep only the protective rules
def shield_v2(h, p=P):
    """Equal weight across all risk assets with a per-asset 20w trend check, each capped at
    1/len(RISK); breadth-graded exposure; idle money in sukuk only if sukuk trends up."""
    last = h.iloc[-1]
    cand = [t for t in RISK if pd.notna(last.get(t))]
    sma = h[cand].iloc[-p["sma"]:].mean()
    up = [t for t in cand if last[t] > sma[t]]
    breadth = len(up) / len(cand)
    expo = 1.0 if breadth >= p["breadth_half"] else 0.5 if breadth >= p["breadth_quarter"] else 0.25
    w = pd.Series(1.0 / len(cand), index=cand)          # equal weight on everything...
    w[[t for t in cand if t not in up]] *= 0.5          # ...half weight while below trend (graded, not binary)
    w = w * expo
    rest = 1 - w.sum()
    s = h["SPSK"]
    if rest > 1e-9 and s.iloc[-1] > s.iloc[-p["sma"]:].mean():
        w["SPSK"] = rest
    return w

if __name__ == "__main__":
    for lab, px in (("PRICE", PX), ("TR", PX_TR)):
        nav, turn = run_tranches(px, shield_v2); m = metrics(nav, turn)
        h1 = run_tranches(px, shield_v2, end=MID)[0].iloc[-1] - 1; h2 = run_tranches(px, shield_v2, start=MID)[0].iloc[-1] - 1
        ends = [nav.iloc[-1 - j] / nav.iloc[0] - 1 for j in range(8)]
        print(f"V2 {lab}: total {m['total']:+.1%} sharpe {m['sharpe']:.2f} maxdd {m['maxdd']:.1%} vol {m['vol']:.1%} "
              f"H1 {h1:+.1%} H2 {h2:+.1%} turnover {turn:.1f} end-range {min(ends):+.1%}..{max(ends):+.1%}")
    for name, ch in [("SMA 13", {"sma": 13}), ("SMA 26", {"sma": 26}), ("no breadth", {"breadth_half": 0, "breadth_quarter": 0})]:
        nav, turn = run_tranches(PX_TR, lambda h, p={**P, **ch}: shield_v2(h, p)); m = metrics(nav, turn)
        print(f"V2 sens {name}: {m['total']:+.1%} dd {m['maxdd']:.1%}")
    w = shield_v2(PX_TR); print("V2 next weights:", (w * 100).round(1).to_dict(), "cash", round((1 - w.sum()) * 100, 1))
