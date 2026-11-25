"""Why did most strategies lose? Diagnostics on the same 52-week window."""
import numpy as np, pandas as pd
import backtest as bt
from backtest import load_prices, UNIVERSE, STRATEGIES, run, metrics
px = load_prices(); px = px.loc[px["SPUS"].notna()]
r = px.pct_change(fill_method=None)
N=len(px); S=N-53; MID=S+26; E=N-1
w = px.iloc[S:]
print("== 1. Market regime (SPUS) by quarter / half")
q = [S, S+13, S+26, S+39, E]
for a,b in zip(q[:-1],q[1:]):
    print(f"  {px.index[a].date()} -> {px.index[b].date()}: SPUS {px.SPUS.iloc[b]/px.SPUS.iloc[a]-1:+.1%}  EW-all {(px.iloc[b]/px.iloc[a]-1).mean():+.1%}")
peak = w.SPUS.idxmax(); print(f"  SPUS peak {peak.date()} {w.SPUS.max():.2f}; end {w.SPUS.iloc[-1]:.2f} ({w.SPUS.iloc[-1]/w.SPUS.max()-1:+.1%})")
print("  last 3 weeks returns:"); print((r.iloc[-3:]*100).round(1).to_string())

print("\n== 2. Momentum signal quality: rank correlation past-k vs next-4w across assets")
for k in (2,4,6,12):
    ics=[]
    for t in range(S, E-4, 1):
        past = px.iloc[t]/px.iloc[t-k]-1; fut = px.iloc[t+4]/px.iloc[t]-1
        d = pd.concat([past,fut],axis=1).dropna()
        ics.append(d.rank().corr().iloc[0,1])
    ics=pd.Series(ics)
    print(f"  k={k:2}: mean IC {ics.mean():+.3f}  share positive {(ics>0).mean():.0%}  H1 {ics[:22].mean():+.3f}  H2 {ics[22:].mean():+.3f}")
print("  time-series autocorr of weekly returns (lag1), per asset:")
print("  ", {t: round(r[t].iloc[S:].autocorr(),2) for t in px.columns})

print("\n== 3. Correlation / diversification")
def avgcorr(a,b):
    c=r.iloc[a:b].corr().values; return c[np.triu_indices_from(c,1)].mean()
print(f"  avg pairwise corr H1 {avgcorr(S,MID):.2f}  H2 {avgcorr(MID,E+1):.2f}  last 8w {avgcorr(E-7,E+1):.2f}")
print(f"  last week: {(r.iloc[-1]<0).sum()} of {r.shape[1]} assets fell")

print("\n== 4. Safe asset & dividends: price-only vs approx total return (window)")
for t,y in [("SPSK",0.045),("SPRE",0.035),("SPUS",0.006),("JNJ",0.025),("XOM",0.034)]:
    pr = w[t].iloc[-1]/w[t].iloc[0]-1
    print(f"  {t}: price {pr:+.1%}  ~yield {y:.1%}  approx total {pr+y:+.1%}")

print("\n== 5. Per-strategy: gross vs cost drag, H1/H2, end-point sensitivity")
rows=[]
for key,name,fam,every,fn,desc in STRATEGIES:
    bt.COST=0.0; g,_,_=run(px,fn,every)
    bt.COST=0.001; n,turn,_=run(px,fn,every)
    h1=n.iloc[26]/n.iloc[0]-1; h2=n.iloc[-1]/n.iloc[26]-1
    rows.append(dict(key=key,total=n.iloc[-1]-1,gross=g.iloc[-1]-1,cost=g.iloc[-1]-n.iloc[-1],h1=h1,h2=h2,
                     ex_last_week=n.iloc[-2]-1, peak=n.max()-1, from_peak=n.iloc[-1]/n.max()-1, turnover=turn))
df=pd.DataFrame(rows).sort_values("total",ascending=False)
pd.set_option("display.width",200)
print((df.set_index("key")*100).round(1).drop(columns="turnover").assign(turnover=df.set_index("key").turnover.round(1)).to_string())
print(f"\n  strategies negative: {(df.total<0).sum()}/{len(df)}  H2 negative: {(df.h2<0).sum()}/{len(df)}  lost from peak >8%: {(df.from_peak<-0.08).sum()}")
print(f"  negative if window ended 1 week earlier: {(df.ex_last_week<0).sum()}")

print("\n== 6. Whipsaw: SMA crossover switches")
sig=[]; 
for t in range(S,E):
    h=px.iloc[:t+1]; sig.append(h.SPUS.iloc[-10:].mean()>h.SPUS.iloc[-30:].mean())
sig=pd.Series(sig,index=px.index[S:E]); sw=sig.ne(sig.shift()).sum()-1
print(f"  switches {sw}; dates:", [str(d.date())+("→SPUS" if v else "→SPSK") for d,v in sig[sig.ne(sig.shift())].items()][1:])
