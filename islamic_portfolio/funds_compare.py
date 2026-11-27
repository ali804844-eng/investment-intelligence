"""Islamic funds vs our strategies over the last 5 years (end Oct-2021 -> Nov-2026), total return.
Funds: Alpha Vantage monthly adjusted closes (distributions reinvested, net of fund fees).
Strategies: weekly backtest on stitched data (data_2020 + data_gap + data), approx. distributions."""
import json, warnings
from pathlib import Path
import numpy as np, pandas as pd
warnings.filterwarnings("ignore")
import backtest as bt
import robust_strategy as rs
HERE = Path(__file__).parent

FUNDS = {  # ticker: (name, type, fee %, note)
    "SPUS": ("SP Funds S&P 500 الشرعي", "صندوق مؤشر – أسهم أمريكية", 0.45, ""),
    "HLAL": ("Wahed FTSE USA Shariah", "صندوق مؤشر – أسهم أمريكية", 0.50, ""),
    "ISWD": ("iShares MSCI World Islamic", "صندوق مؤشر – أسهم عالمية", 0.30, "مسعّر بالجنيه الإسترليني"),
    "UMMA": ("Wahed داو جونز الإسلامي العالمي (خارج أمريكا)", "صندوق مؤشر – أسهم دولية", 0.65, "يبدأ فبراير 2022"),
    "AMAGX": ("أمانة للنمو Amana Growth", "صندوق مُدار – أسهم نمو", 0.87, ""),
    "AMANX": ("أمانة للدخل Amana Income", "صندوق مُدار – أسهم توزيعات", 1.00, ""),
    "SPRE": ("SP Funds للعقار الشرعي", "صندوق مؤشر – عقار", 0.69, ""),
    "SPSK": ("SP Funds للصكوك العالمية", "صندوق مؤشر – صكوك", 0.55, ""),
    "AMAPX": ("أمانة للمشاركة Amana Participation", "صندوق مُدار – صكوك", 0.82, ""),
}

def stats_monthly(s):
    r = s.pct_change().dropna()
    yrs = len(r) / 12
    tot = s.iloc[-1] / s.iloc[0] - 1
    return dict(total=tot, cagr=(1 + tot) ** (1 / yrs) - 1, vol=r.std() * np.sqrt(12),
                maxdd=(s / s.cummax() - 1).min(), y2022=yr(s, 2022), y2023=yr(s, 2023), y2024=yr(s, 2024), y2025=yr(s, 2025),
                ytd2026=s.iloc[-1] / s[s.index.str.startswith("2025-12")].iloc[0] - 1)

def yr(s, y):
    a = s[s.index.str.startswith(f"{y-1}-12")]; b = s[s.index.str.startswith(f"{y}-12")]
    return float(b.iloc[0] / a.iloc[0] - 1) if len(a) and len(b) else None

def load_weekly():
    frames = {}
    for t in rs.YIELD:
        parts = [pd.read_csv(HERE / d / f"{t}.csv", parse_dates=["date"]) for d in ("data_2020", "data_gap", "data") if (HERE / d / f"{t}.csv").exists()]
        if not parts or t == "AMANX": continue
        s = pd.concat(parts).drop_duplicates("date", keep="last").set_index("date")["close"].sort_index()
        frames[t] = s
    return pd.DataFrame(frames).sort_index()

if __name__ == "__main__":
    rows, curves = [], {}
    for t, (nm, typ, fee, note) in FUNDS.items():
        s = pd.read_csv(HERE / "funds" / f"{t}.csv", dtype={"month": str}).set_index("month")["adj_close"]
        st = stats_monthly(s)
        rows.append(dict(key=t, name=nm, type=typ, fee=fee / 100, note=note, start=s.index[0], **st))
        curves[t] = (s / s.iloc[0] * 100).round(2)
    # strategies on weekly data, window = last Friday of Oct-2021 .. last week available
    px = rs.total_return(load_weekly())
    gaps = px.loc["2021-10-01":].isna().sum(); print("missing weeks per asset:", gaps[gaps > 0].to_dict())
    S = px.index.get_indexer([pd.Timestamp("2021-10-25")], method="nearest")[0]; E = len(px) - 1
    bt.COST = 0.001
    strat = {"shield_v2": ("الدرع التكيفي (استراتيجيتنا)", lambda: rs.run_tranches(px, rs.shield_v2, start=S, end=E)),
             "mom_4_3": ("مضاربة: زخم أسبوعي – أقوى 3", lambda: rs.run_single(px, bt.make_momentum(4, 3), 1, start=S, end=E)),
             "stocks_ew": ("الأسهم السبعة بأوزان متساوية", lambda: rs.run_single(px, bt.stocks_only_ew, 4, start=S, end=E))}
    for k, (nm, f) in strat.items():
        nav, turn = f()
        m = nav.resample("ME").last(); m.index = m.index.strftime("%Y-%m")
        st = stats_monthly(m)
        rows.append(dict(key=k, name=nm, type="استراتيجية (اختبار تاريخي)", fee=None, note="بدون رسوم صناديق؛ تكلفة تداول 0.1%", start=m.index[0], **st))
        curves[k] = (m / m.iloc[0] * 100).round(2)
    df = pd.DataFrame(rows).set_index("key").sort_values("cagr", ascending=False)
    pd.set_option("display.width", 220)
    show = df.drop(columns=["name", "type", "note"]).copy()
    for c in show.columns:
        if c not in ("start",): show[c] = (show[c].astype(float) * 100).round(1)
    print(show.to_string())
    json.dump({"table": json.loads(df.to_json(orient="index")), "curves": {k: v.to_dict() for k, v in curves.items()}},
              open(HERE / "funds_results.json", "w"), ensure_ascii=False)
