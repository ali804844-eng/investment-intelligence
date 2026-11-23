"""Backtest of portfolio strategies on a Sharia-compliant global universe.

Data: weekly closes (Twelve Data) stored in ./data/*.csv.
Test window: the last 52 weeks. Earlier weeks are used only as look-back
for signals, so every decision uses information available at that time
(signals at week t close -> weights applied to return of week t+1).
Transaction costs: 10 bps per unit of turnover.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
TEST_WEEKS = 52
COST = 0.0010  # 10 bps per unit of turnover
WEEKS_PER_YEAR = 52

UNIVERSE = {
    # ticker: (Arabic name, type, region/sector)
    "SPUS": ("صندوق SP Funds S&P 500 المتوافق مع الشريعة", "صندوق", "أسهم أمريكية"),
    "HLAL": ("صندوق Wahed FTSE USA Shariah", "صندوق", "أسهم أمريكية"),
    "UMMA": ("صندوق Wahed داو جونز الإسلامي العالمي (خارج أمريكا)", "صندوق", "أسهم عالمية"),
    "AMANX": ("صندوق أمانة للنمو (Amana Growth)", "صندوق", "أسهم عالمية"),
    "SPSK": ("صندوق SP Funds للصكوك العالمية", "صندوق", "صكوك"),
    "SPRE": ("صندوق SP Funds للعقار المتوافق مع الشريعة", "صندوق", "عقار"),
    "AAPL": ("أبل", "سهم", "تقنية"),
    "MSFT": ("مايكروسوفت", "سهم", "تقنية"),
    "NVDA": ("إنفيديا", "سهم", "أشباه موصلات"),
    "TSM": ("تايوان لأشباه الموصلات", "سهم", "أشباه موصلات"),
    "LLY": ("إيلي ليلي", "سهم", "رعاية صحية"),
    "JNJ": ("جونسون آند جونسون", "سهم", "رعاية صحية"),
    "XOM": ("إكسون موبيل", "سهم", "طاقة"),
}


def load_prices() -> pd.DataFrame:
    frames = {}
    for t in UNIVERSE:
        f = HERE / "data" / f"{t}.csv"
        if f.exists():
            s = pd.read_csv(f, parse_dates=["date"]).set_index("date")["close"]
            frames[t] = s.sort_index()
    px = pd.DataFrame(frames).sort_index()
    return px


# ---------------------------------------------------------------- strategies
# Each strategy: f(hist_prices: DataFrame up to and including week t) -> weights Series
def _avail(h):
    return h.columns[h.iloc[-1].notna()]


def equal_weight(h):
    c = _avail(h)
    return pd.Series(1 / len(c), index=c)


def buy_and_hold_spus(h):
    return pd.Series({"SPUS": 1.0})


def classic_60_40(h):
    return pd.Series({"SPUS": 0.4, "UMMA": 0.2, "SPSK": 0.4})


def _vol(h, n=20):
    r = h.pct_change(fill_method=None).iloc[-n:]
    return r.std()


def inverse_volatility(h):
    v = _vol(h)[_avail(h)].dropna()
    w = 1 / v
    return w / w.sum()


def min_variance(h, n=26):
    r = h[_avail(h)].pct_change(fill_method=None).iloc[-n:].dropna(axis=1)
    cov = r.cov().values + np.eye(r.shape[1]) * 1e-6
    w = np.ones(r.shape[1]) / r.shape[1]
    # projected gradient descent on simplex (long only)
    for _ in range(500):
        g = cov @ w
        w = w - 5 * g
        w = _proj_simplex(w)
    return pd.Series(w, index=r.columns)


def _proj_simplex(v):
    u = np.sort(v)[::-1]
    css = np.cumsum(u)
    rho = np.nonzero(u * np.arange(1, len(v) + 1) > (css - 1))[0][-1]
    theta = (css[rho] - 1) / (rho + 1)
    return np.maximum(v - theta, 0)


def max_sharpe(h, n=26):
    r = h[_avail(h)].pct_change(fill_method=None).iloc[-n:].dropna(axis=1)
    mu = r.mean().values
    cov = r.cov().values + np.eye(r.shape[1]) * 1e-6
    w = np.ones(r.shape[1]) / r.shape[1]
    for _ in range(800):
        pv = np.sqrt(w @ cov @ w)
        pr = w @ mu
        grad = mu / pv - pr * (cov @ w) / pv ** 3
        w = _proj_simplex(w + 0.02 * grad)
        w = np.minimum(w, 0.35)  # concentration cap
        w = w / w.sum()
    return pd.Series(w, index=r.columns)


def risk_parity(h, n=26):
    r = h[_avail(h)].pct_change(fill_method=None).iloc[-n:].dropna(axis=1)
    cov = r.cov().values
    n = r.shape[1]
    b = np.ones(n) / n
    y = 1 / np.sqrt(np.diag(cov))
    # cyclical coordinate descent (Griveau-Billion et al.) for equal risk contribution
    for _ in range(200):
        for i in range(n):
            a = cov[i, i]
            c = cov[i] @ y - a * y[i]
            y[i] = (-c + np.sqrt(c * c + 4 * a * b[i])) / (2 * a)
    w = y / y.sum()
    return pd.Series(w, index=r.columns)


def make_momentum(lookback, top):
    def f(h):
        c = _avail(h)
        m = (h[c].iloc[-1] / h[c].iloc[-1 - lookback] - 1).dropna()
        best = m.sort_values(ascending=False).head(top)
        best = best[best > 0]
        if best.empty:
            return pd.Series({"SPSK": 1.0})  # defensive: sukuk
        return pd.Series(1 / len(best), index=best.index)
    return f


def dual_momentum(h, lookback=12):
    # absolute + relative momentum across equity funds, sukuk as safe asset
    eq = ["SPUS", "UMMA", "SPRE"]
    m = (h[eq].iloc[-1] / h[eq].iloc[-1 - lookback] - 1)
    best = m.idxmax()
    safe = h["SPSK"].iloc[-1] / h["SPSK"].iloc[-1 - lookback] - 1
    return pd.Series({best if m[best] > safe else "SPSK": 1.0})


def trend_following(h, n=20):
    # hold each asset equally only while its price is above its n-week SMA, else sukuk
    c = _avail(h)
    sma = h[c].iloc[-n:].mean()
    on = [t for t in c if h[t].iloc[-1] > sma[t] and t != "SPSK"]
    k = len(c) - (1 if "SPSK" in c else 0)
    w = pd.Series(0.0, index=c)
    for t in on:
        w[t] = 1 / k
    w["SPSK"] = w.get("SPSK", 0) + (1 - w.sum())
    return w


def sma_crossover_spus(h):
    fast, slow = h["SPUS"].iloc[-10:].mean(), h["SPUS"].iloc[-30:].mean()
    return pd.Series({"SPUS": 1.0} if fast > slow else {"SPSK": 1.0})


def mean_reversion(h, lookback=4, bottom=4):
    c = [t for t in _avail(h) if t != "SPSK"]
    m = (h[c].iloc[-1] / h[c].iloc[-1 - lookback] - 1).dropna()
    worst = m.sort_values().head(bottom)
    return pd.Series(1 / len(worst), index=worst.index)


def vol_target(h, target=0.12):
    # SPUS scaled to 12% annual vol, rest in sukuk
    v = h["SPUS"].pct_change().iloc[-12:].std() * np.sqrt(52)
    x = float(min(1.0, target / v))
    return pd.Series({"SPUS": x, "SPSK": 1 - x})


def stocks_only_ew(h):
    c = [t for t in _avail(h) if UNIVERSE[t][1] == "سهم"]
    return pd.Series(1 / len(c), index=c)


def funds_only_ew(h):
    c = [t for t in _avail(h) if UNIVERSE[t][1] == "صندوق"]
    return pd.Series(1 / len(c), index=c)


def core_satellite(h):
    # 60% core in funds, 40% satellite in 3 best stocks by 12-week momentum
    stocks = [t for t in _avail(h) if UNIVERSE[t][1] == "سهم"]
    m = (h[stocks].iloc[-1] / h[stocks].iloc[-13] - 1).sort_values(ascending=False).head(3)
    w = pd.Series({"SPUS": 0.3, "UMMA": 0.2, "SPSK": 0.1})
    for t in m.index:
        w[t] = 0.4 / 3
    return w


# (key, Arabic name, family, rebalance every N weeks, fn, description)
STRATEGIES = [
    ("bh_spus", "شراء واحتفاظ – SPUS", "مرجعية", 52, buy_and_hold_spus,
     "استثمار كامل في صندوق S&P 500 الشرعي دون أي تداول. هذا هو المؤشر المرجعي."),
    ("ew_monthly", "أوزان متساوية (شهري)", "توزيع ثابت", 4, equal_weight,
     "توزيع متساوٍ على كل الأصول الـ13 مع إعادة توازن كل 4 أسابيع."),
    ("ew_quarterly", "أوزان متساوية (ربع سنوي)", "توزيع ثابت", 13, equal_weight,
     "نفس الفكرة مع إعادة توازن كل ربع سنة لتقليل التكاليف."),
    ("bal_60_40", "متوازنة 60/40 (أسهم/صكوك)", "توزيع ثابت", 13, classic_60_40,
     "40% SPUS + 20% UMMA + 40% صكوك SPSK – المحفظة التقليدية المتحفظة."),
    ("funds_ew", "الصناديق فقط – أوزان متساوية", "توزيع ثابت", 4, funds_only_ew,
     "توزيع متساوٍ على الصناديق الإسلامية الستة فقط."),
    ("stocks_ew", "الأسهم فقط – أوزان متساوية", "توزيع ثابت", 4, stocks_only_ew,
     "توزيع متساوٍ على الأسهم السبعة المتوافقة فقط."),
    ("core_sat", "النواة والأقمار", "هجينة", 4, core_satellite,
     "60% نواة من الصناديق + 40% في أقوى 3 أسهم زخماً خلال 12 أسبوعاً."),
    ("inv_vol", "معكوس التذبذب", "مخاطر", 4, inverse_volatility,
     "وزن كل أصل يتناسب عكسياً مع تذبذبه خلال آخر 20 أسبوعاً."),
    ("risk_parity", "تكافؤ المخاطر", "مخاطر", 4, risk_parity,
     "يساهم كل أصل بنفس القدر في مخاطر المحفظة الكلية (يأخذ الارتباطات بالحسبان)."),
    ("min_var", "أدنى تباين (ماركويتز)", "أمثلية", 4, min_variance,
     "المحفظة الأقل تذبذباً على حدود ماركويتز (بيع على المكشوف ممنوع)."),
    ("max_sharpe", "أعلى نسبة شارب (ماركويتز)", "أمثلية", 4, max_sharpe,
     "تعظيم العائد لكل وحدة مخاطرة حسب آخر 26 أسبوعاً، بحد أقصى 35% للأصل."),
    ("mom_12_3", "زخم 12 أسبوعاً – أفضل 3", "زخم", 4, make_momentum(12, 3),
     "شراء أقوى 3 أصول أداءً خلال 12 أسبوعاً؛ إن لم يكن أي منها موجباً نلجأ للصكوك."),
    ("mom_26_5", "زخم 26 أسبوعاً – أفضل 5", "زخم", 4, make_momentum(26, 5),
     "شراء أقوى 5 أصول خلال نصف سنة مع إعادة توازن شهرية."),
    ("mom_4_3", "زخم قصير 4 أسابيع – أفضل 3", "زخم", 1, make_momentum(4, 3),
     "تدوير أسبوعي نحو أقوى 3 أصول خلال الشهر الماضي."),
    ("mom_4_3m", "زخم 4 أسابيع – أفضل 3 (شهري)", "زخم", 4, make_momentum(4, 3),
     "نفس إشارة الزخم القصير لكن مع إعادة توازن كل 4 أسابيع فقط لخفض التداول والتكاليف."),
    ("dual_mom", "الزخم المزدوج", "زخم", 4, dual_momentum,
     "اختيار أقوى صندوق أسهم (SPUS/UMMA/SPRE)، والتحول للصكوك إن كان أداؤه أقل منها."),
    ("trend", "تتبع الاتجاه (متوسط 20 أسبوعاً)", "اتجاه", 1, trend_following,
     "الاحتفاظ بأي أصل فوق متوسطه المتحرك لـ20 أسبوعاً، والباقي في الصكوك."),
    ("sma_x", "تقاطع المتوسطات على SPUS", "اتجاه", 1, sma_crossover_spus,
     "SPUS عندما يكون المتوسط 10 أسابيع فوق 30 أسبوعاً، وإلا صكوك."),
    ("mean_rev", "الارتداد للمتوسط", "عكسية", 1, mean_reversion,
     "شراء أسوأ 4 أصول أداءً في آخر 4 أسابيع على أمل الارتداد."),
    ("vol_target", "استهداف تذبذب 12%", "مخاطر", 1, vol_target,
     "تعديل نسبة SPUS أسبوعياً ليبقى تذبذب المحفظة قرب 12% والباقي صكوك."),
]


def run(px: pd.DataFrame, fn, every: int):
    rets = px.pct_change(fill_method=None)
    start = len(px) - TEST_WEEKS - 1  # signal at this index, first return next week
    dates, nav, w_hist, turn = [], [1.0], [], 0.0
    w = pd.Series(dtype=float)
    for i, t in enumerate(range(start, len(px) - 1)):
        if i % every == 0:
            new = fn(px.iloc[: t + 1]).reindex(px.columns).fillna(0.0)
            tr = (new - w.reindex(px.columns).fillna(0.0)).abs().sum()
            turn += tr
            w = new
        r = float((w * rets.iloc[t + 1].fillna(0.0)).sum())
        cost = COST * tr if i % every == 0 else 0.0
        nav.append(nav[-1] * (1 + r - cost))
        # drift weights
        grown = w * (1 + rets.iloc[t + 1].fillna(0.0))
        w = grown / grown.sum() if grown.sum() > 0 else grown
        w_hist.append(w)
        dates.append(px.index[t + 1])
    dates = [px.index[start]] + dates
    nav = pd.Series(nav, index=dates)
    return nav, turn, w_hist[-1]


def metrics(nav: pd.Series, turnover: float, bench: pd.Series | None = None):
    r = nav.pct_change().dropna()
    total = nav.iloc[-1] / nav.iloc[0] - 1
    vol = r.std() * np.sqrt(WEEKS_PER_YEAR)
    ann = (1 + total) ** (WEEKS_PER_YEAR / len(r)) - 1
    sharpe = (r.mean() * WEEKS_PER_YEAR - 0.04) / vol if vol > 0 else 0  # 4% cash proxy
    dn = r[r < 0].std() * np.sqrt(WEEKS_PER_YEAR)
    sortino = (r.mean() * WEEKS_PER_YEAR - 0.04) / dn if dn > 0 else 0
    dd = (nav / nav.cummax() - 1).min()
    calmar = ann / abs(dd) if dd < 0 else 0
    win = (r > 0).mean()
    out = dict(total=total, ann=ann, vol=vol, sharpe=sharpe, sortino=sortino,
               maxdd=dd, calmar=calmar, win=win, turnover=turnover)
    if bench is not None:
        br = bench.pct_change().dropna()
        beta = np.cov(r, br)[0, 1] / br.var()
        out["beta"] = beta
        out["alpha"] = (r.mean() - beta * br.mean()) * WEEKS_PER_YEAR
    return out


def main():
    px = load_prices()
    px = px.loc[px["SPUS"].notna()]
    results, navs = [], {}
    bench_nav, _, _ = run(px, buy_and_hold_spus, 52)
    for key, name, fam, every, fn, desc in STRATEGIES:
        nav, turn, last_w = run(px, fn, every)
        m = metrics(nav, turn, bench_nav)
        # composite score: rank-average of Sharpe, total return, Calmar, maxDD
        results.append(dict(key=key, name=name, family=fam, every=every, desc=desc, **m,
                            weights={k: round(float(v), 4) for k, v in last_w.items() if v > 0.001}))
        navs[key] = nav
    df = pd.DataFrame(results)
    for col, asc in [("sharpe", False), ("total", False), ("calmar", False), ("maxdd", False)]:
        df["r_" + col] = df[col].rank(ascending=asc)
    df["score"] = (df.r_sharpe * 0.4 + df.r_total * 0.3 + df.r_calmar * 0.2 + df.r_maxdd * 0.1).round(2)
    df = df.sort_values(["score", "sharpe"], ascending=[True, False]).reset_index(drop=True)
    df["rank"] = np.arange(1, len(df) + 1)

    # asset-level stats over the same window
    sub = px.iloc[-TEST_WEEKS - 1:]
    assets = []
    for t, (nm, typ, sec) in UNIVERSE.items():
        if t not in sub:
            continue
        s = sub[t].dropna()
        r = s.pct_change().dropna()
        assets.append(dict(ticker=t, name=nm, type=typ, sector=sec,
                           ret=float(s.iloc[-1] / s.iloc[0] - 1),
                           vol=float(r.std() * np.sqrt(52)),
                           maxdd=float((s / s.cummax() - 1).min()),
                           spark=[round(float(x / s.iloc[0] * 100), 2) for x in s]))
    corr = sub.pct_change().dropna().corr().round(2)

    out = dict(
        period=dict(start=str(navs["bh_spus"].index[0].date()), end=str(navs["bh_spus"].index[-1].date()),
                    weeks=TEST_WEEKS, cost_bps=COST * 1e4),
        strategies=json.loads(df.drop(columns=[c for c in df if c.startswith("r_")]).to_json(orient="records")),
        nav={"dates": [str(d.date()) for d in navs["bh_spus"].index],
             **{k: [round(float(x) * 100, 2) for x in v] for k, v in navs.items()}},
        dd={k: [round(float(x) * 100, 2) for x in (v / v.cummax() - 1)] for k, v in navs.items()},
        assets=assets,
        corr=dict(labels=list(corr.columns), values=corr.values.tolist()),
    )
    (HERE / "results.json").write_text(json.dumps(out, ensure_ascii=False))
    pd.set_option("display.width", 200)
    print(df[["rank", "key", "total", "ann", "vol", "sharpe", "maxdd", "calmar", "turnover", "score"]]
          .round(3).to_string())


if __name__ == "__main__":
    main()


def robustness():
    """Sensitivity of the momentum family to look-back, top-N and cost."""
    global COST
    px = load_prices()
    px = px.loc[px["SPUS"].notna()]
    grid = []
    for lb in (2, 3, 4, 6, 8, 12):
        for top in (2, 3, 4, 5):
            nav, turn, _ = run(px, make_momentum(lb, top), 1)
            m = metrics(nav, turn)
            grid.append(dict(lookback=lb, top=top, total=round(m["total"], 4), sharpe=round(m["sharpe"], 3),
                             maxdd=round(m["maxdd"], 4)))
    costs = []
    base = COST
    for c in (0.0, 0.001, 0.002, 0.003, 0.005):
        COST = c
        nav, turn, _ = run(px, make_momentum(4, 3), 1)
        costs.append(dict(bps=c * 1e4, total=round(nav.iloc[-1] - 1, 4)))
    COST = base
    # same strategy rebalanced monthly instead of weekly
    nav, turn, _ = run(px, make_momentum(4, 3), 4)
    monthly = metrics(nav, turn)
    return dict(grid=grid, costs=costs, monthly=dict(total=monthly["total"], sharpe=monthly["sharpe"],
                                                     maxdd=monthly["maxdd"], turnover=monthly["turnover"]))


if __name__ == "__main__":
    rb = robustness()
    p = HERE / "results.json"
    d = json.loads(p.read_text())
    d["robust"] = rb
    p.write_text(json.dumps(d, ensure_ascii=False))
    import statistics
    tots = [g["total"] for g in rb["grid"]]
    print("grid median total", statistics.median(tots), "positive", sum(t > 0 for t in tots), "/", len(tots))
    print("beat SPUS", sum(t > 0.042 for t in tots))
    print(rb["costs"], rb["monthly"])
    print(sorted(rb["grid"], key=lambda g: -g["total"])[:5])
