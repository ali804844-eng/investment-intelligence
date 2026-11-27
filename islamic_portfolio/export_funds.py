import json
from pathlib import Path
HERE = Path(__file__).parent; OUT = HERE / "dashboard"
d = json.load(open(HERE / "funds_results.json"))
meta = [{"label": f, "value": (HERE / f).read_text(), "type": "code", "lang": "python"} for f in ("funds_compare.py", "export_funds.py")]
meta.append({"label": "Input", "value": "Alpha Vantage monthly adjusted closes (funds/*.csv, distributions reinvested, net of fund fees); strategies: weekly Twelve Data closes with approximate distributions"})
r4 = lambda x: None if x is None else round(float(x), 4)
rows = []
for k, v in d["table"].items():
    rows.append({"key": k, "name": v["name"], "type": v["type"], "kind": "strategy" if v["fee"] is None else "fund",
                 "fee": r4(v["fee"]), "note": v["note"], **{c: r4(v[c]) for c in ("total", "cagr", "vol", "maxdd", "y2022", "y2023", "y2024", "y2025", "ytd2026")}})
rows.sort(key=lambda r: -r["cagr"])
curves = [{"month": m, "key": k, "value": val} for k in ("shield_v2", "SPUS", "HLAL", "AMANX", "AMAGX", "SPSK", "mom_4_3") for m, val in d["curves"][k].items()]
hedge = [{"year": "2023", "hfri": 0.075, "source": "HFR (تقدير يناير 2024)"}, {"year": "2024", "hfri": 0.098, "source": "HFR"}, {"year": "2025", "hfri": 0.125, "source": "HFR (يناير 2026)"}]
for n, data in (("funds_summary", rows), ("funds_curves", curves), ("hedge_funds", hedge)):
    m = meta if n != "hedge_funds" else [{"label": "Source", "value": "HFRI Fund Weighted Composite: hfr.com year-end releases (2023 +7.5% est., 2024 +9.8%, 2025 +12.5%). 2022 not confirmed."}]
    (OUT / f"{n}.json").write_text(json.dumps({"rows": data, "meta": m}, ensure_ascii=False)); print(n, len(data))
