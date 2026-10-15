"""Point-in-time data panel and feature engine.

The same ``Panel.features(security_id, as_of)`` code path is used for live screening (as_of = latest date) and
for historical backtests (as_of = each rebalance date). Point-in-time rules:
  * prices: only bars dated <= as_of
  * fundamentals: only facts whose ``filed_date`` <= as_of (facts without a filing date are excluded)
This prevents look-ahead bias by construction.
"""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import pandas as pd
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.finance import ratios
from app.models import FundamentalFact, FxRate, PriceBar, Security

FEATURES: dict[str, dict] = {
    # fundamentals
    "revenue_cagr_3y": {"label": "Revenue CAGR 3y", "fmt": "pct", "group": "growth"},
    "eps_cagr_3y": {"label": "EPS CAGR 3y", "fmt": "pct", "group": "growth"},
    "gross_margin": {"label": "Gross margin", "fmt": "pct", "group": "profitability"},
    "operating_margin": {"label": "Operating margin", "fmt": "pct", "group": "profitability"},
    "operating_margin_stability": {"label": "Op. margin st.dev (5y)", "fmt": "pct", "group": "quality"},
    "fcf_margin": {"label": "FCF margin", "fmt": "pct", "group": "cash flow"},
    "fcf_consistency": {"label": "Share of years with positive FCF", "fmt": "pct", "group": "cash flow"},
    "roic": {"label": "ROIC (latest)", "fmt": "pct", "group": "returns"},
    "roic_avg": {"label": "ROIC (5y avg)", "fmt": "pct", "group": "returns"},
    "roe": {"label": "ROE", "fmt": "pct", "group": "returns"},
    "debt_to_equity": {"label": "Debt / equity", "fmt": "x", "group": "balance sheet"},
    "net_debt_to_ebitda": {"label": "Net debt / EBITDA", "fmt": "x", "group": "balance sheet"},
    "interest_coverage": {"label": "Interest coverage (EBIT / interest)", "fmt": "x", "group": "balance sheet"},
    "current_ratio": {"label": "Current ratio", "fmt": "x", "group": "balance sheet"},
    "share_count_cagr_3y": {"label": "Share count CAGR 3y (dilution)", "fmt": "pct", "group": "quality"},
    "sbc_to_revenue": {"label": "SBC / revenue", "fmt": "pct", "group": "quality"},
    "accruals_ratio": {"label": "Accruals ratio", "fmt": "pct", "group": "quality"},
    "payout_ratio": {"label": "Dividend payout (of NI)", "fmt": "pct", "group": "income"},
    "fcf_payout_ratio": {"label": "Dividend payout (of FCF)", "fmt": "pct", "group": "income"},
    # valuation
    "market_cap_usd": {"label": "Market cap (USD)", "fmt": "money", "group": "valuation"},
    "pe": {"label": "P/E", "fmt": "x", "group": "valuation"},
    "ps": {"label": "P/S", "fmt": "x", "group": "valuation"},
    "pb": {"label": "P/B", "fmt": "x", "group": "valuation"},
    "ev_ebitda": {"label": "EV/EBITDA", "fmt": "x", "group": "valuation"},
    "fcf_yield": {"label": "FCF yield", "fmt": "pct", "group": "valuation"},
    "earnings_yield": {"label": "Earnings yield", "fmt": "pct", "group": "valuation"},
    "dividend_yield": {"label": "Dividend yield", "fmt": "pct", "group": "income"},
    # price-based
    "price": {"label": "Price (local)", "fmt": "num", "group": "price"},
    "mom_12_1": {"label": "Momentum 12-1m", "fmt": "pct", "group": "momentum"},
    "mom_6m": {"label": "Return 6m", "fmt": "pct", "group": "momentum"},
    "ret_1m": {"label": "Return 1m", "fmt": "pct", "group": "momentum"},
    "vol_60d": {"label": "Volatility 60d (ann.)", "fmt": "pct", "group": "risk"},
    "vol_252d": {"label": "Volatility 1y (ann.)", "fmt": "pct", "group": "risk"},
    "max_dd_1y": {"label": "Max drawdown 1y", "fmt": "pct", "group": "risk"},
    "adv_usd_20d": {"label": "Avg daily $ volume 20d", "fmt": "money", "group": "liquidity"},
    "dist_52w_high": {"label": "Distance from 52w high", "fmt": "pct", "group": "momentum"},
    "above_sma200": {"label": "Above 200d SMA", "fmt": "bool", "group": "trend"},
    "sma50_over_sma200": {"label": "SMA50 / SMA200 − 1", "fmt": "pct", "group": "trend"},
    "rsi14": {"label": "RSI(14)", "fmt": "num", "group": "momentum"},
    # data age
    "fundamentals_age_days": {"label": "Days since latest fiscal period end", "fmt": "num", "group": "data"},
    "price_age_days": {"label": "Days since latest price", "fmt": "num", "group": "data"},
}


@dataclass
class SecMeta:
    id: int
    symbol: str
    name: str
    asset_class: str
    currency: str
    country: str | None
    sector: str | None
    industry: str | None
    is_sample: bool


@dataclass
class Panel:
    securities: dict[int, SecMeta]
    close: pd.DataFrame  # index: date, columns: security id (adjusted where available)
    volume: pd.DataFrame
    facts: dict[int, list[tuple[date, date, str, float]]]  # (filed, period_end, metric, value), sorted by filed
    fx_usd: dict[str, pd.Series] = field(default_factory=dict)  # USD per 1 unit of currency
    _stmt_cache: dict = field(default_factory=dict)
    _feat_cache: dict = field(default_factory=dict)

    @property
    def dates(self) -> list[date]:
        return list(self.close.index)

    def fx_to_usd(self, ccy: str, on: date) -> float | None:
        if ccy == "USD":
            return 1.0
        s = self.fx_usd.get(ccy)
        if s is None or s.empty:
            return None
        i = s.index.searchsorted(on, side="right") - 1
        return float(s.iloc[i]) if i >= 0 else None

    def statements_asof(self, sid: int, as_of: date) -> dict:
        rows = self.facts.get(sid, [])
        filed = [r[0] for r in rows]
        k = bisect.bisect_right(filed, as_of)
        key = (sid, k)
        if key not in self._stmt_cache:
            stm: dict[str, dict[str, float]] = {}
            for _, pe, m, v in rows[:k]:
                stm.setdefault(m, {})[pe.isoformat()] = v  # later filings overwrite restated values
            self._stmt_cache[key] = stm
        return self._stmt_cache[key]

    def features(self, sid: int, as_of: date) -> dict:
        key = (sid, as_of)
        if key not in self._feat_cache:
            self._feat_cache[key] = self._compute_features(sid, as_of)
        return dict(self._feat_cache[key])

    def _compute_features(self, sid: int, as_of: date) -> dict:
        meta = self.securities[sid]
        f: dict[str, float | None] = {k: None for k in FEATURES}
        # ---- prices (<= as_of)
        if sid in self.close.columns:
            s = self.close[sid].loc[: as_of].dropna()
            v = self.volume[sid].loc[: as_of].dropna() if sid in self.volume.columns else pd.Series(dtype=float)
        else:
            s = pd.Series(dtype=float)
            v = s
        px = None
        if len(s):
            px = float(s.iloc[-1])
            f["price"] = px
            f["price_age_days"] = (as_of - s.index[-1]).days
            arr = s.values
            n = len(arr)
            if n > 252:
                f["mom_12_1"] = arr[-22] / arr[-253] - 1
            if n > 126:
                f["mom_6m"] = arr[-1] / arr[-127] - 1
            if n > 21:
                f["ret_1m"] = arr[-1] / arr[-22] - 1
            rets = np.diff(arr) / arr[:-1]
            if len(rets) >= 60:
                f["vol_60d"] = float(np.std(rets[-60:], ddof=1) * math.sqrt(252))
            if len(rets) >= 240:
                f["vol_252d"] = float(np.std(rets[-252:], ddof=1) * math.sqrt(252))
            if n >= 200:
                w = arr[-252:]
                peak = np.maximum.accumulate(w)
                f["max_dd_1y"] = float((w / peak - 1).min())
                f["dist_52w_high"] = float(arr[-1] / w.max() - 1)
                sma200 = arr[-200:].mean()
                sma50 = arr[-50:].mean()
                f["above_sma200"] = 1.0 if arr[-1] > sma200 else 0.0
                f["sma50_over_sma200"] = float(sma50 / sma200 - 1)
            if n > 15:
                d = np.diff(arr[-15:])
                g, lo = d[d > 0].sum() / 14, -d[d < 0].sum() / 14
                f["rsi14"] = 100.0 if lo == 0 else float(100 - 100 / (1 + g / lo))
            if len(v) >= 20:
                fx = self.fx_to_usd(meta.currency, as_of)
                dv = (s.loc[v.index[-20:]].values * v.values[-20:]).mean() if len(s) >= 20 else None
                if dv is not None and fx is not None:
                    f["adv_usd_20d"] = float(dv * fx)
        # ---- fundamentals (filed <= as_of)
        if meta.asset_class == "equity":
            stm = self.statements_asof(sid, as_of)
            if stm:
                an = ratios.analyze(stm)
                sm = an["summary"]
                latest = sm.get("latest_period")
                f["revenue_cagr_3y"] = sm.get("revenue_cagr_3y")
                f["eps_cagr_3y"] = sm.get("eps_cagr_3y")
                f["fcf_consistency"] = sm.get("fcf_consistency")
                f["roic_avg"] = sm.get("roic_avg")
                f["operating_margin_stability"] = sm.get("operating_margin_stability")
                f["share_count_cagr_3y"] = sm.get("share_count_cagr_3y")
                for k in ("gross_margin", "operating_margin", "fcf_margin", "roic", "roe", "debt_to_equity",
                          "net_debt_to_ebitda", "interest_coverage", "current_ratio", "sbc_to_revenue",
                          "accruals_ratio", "payout_ratio", "fcf_payout_ratio"):
                    f[k] = sm.get(f"latest_{k}")
                if latest:
                    f["fundamentals_age_days"] = (as_of - date.fromisoformat(latest)).days
                    latest_vals = {m: vals.get(latest) for m, vals in stm.items()}
                    mult = ratios.market_multiples(px, latest_vals, an["by_period"][latest])
                    for k in ("pe", "ps", "pb", "ev_ebitda", "fcf_yield", "earnings_yield", "dividend_yield"):
                        f[k] = mult.get(k)
                    if mult.get("market_cap") is not None:
                        fx = self.fx_to_usd(meta.currency, as_of)
                        f["market_cap_usd"] = mult["market_cap"] * fx if fx else None
        out = {}
        for k, val in f.items():
            if val is None:
                out[k] = None
            else:
                fv = float(val)
                out[k] = fv if math.isfinite(fv) else None
        return out


def load_panel(db: Session, security_ids: list[int] | None = None, start: date | None = None,
               asset_classes: tuple[str, ...] | None = None, include_sample: bool | None = None) -> Panel:
    q = select(Security).where(Security.is_active.is_(True))
    if security_ids is not None:
        q = q.where(Security.id.in_(security_ids))
    if asset_classes:
        q = q.where(Security.asset_class.in_(asset_classes))
    if include_sample is not None:
        q = q.where(Security.is_sample.is_(include_sample))
    secs = db.scalars(q).all()
    metas = {s.id: SecMeta(s.id, s.symbol, s.name, s.asset_class, s.currency, s.country, s.sector, s.industry, s.is_sample)
             for s in secs}
    ids = list(metas)
    start = start or date(2014, 1, 1)
    if ids:
        rows = db.execute(select(PriceBar.security_id, PriceBar.date, PriceBar.close, PriceBar.adj_close, PriceBar.volume)
                          .where(PriceBar.security_id.in_(ids), PriceBar.date >= start)).all()
    else:
        rows = []
    if rows:
        df = pd.DataFrame(rows, columns=["sid", "date", "close", "adj", "volume"])
        df["px"] = df["adj"].fillna(df["close"])
        close = df.pivot(index="date", columns="sid", values="px").sort_index()
        volume = df.pivot(index="date", columns="sid", values="volume").sort_index()
    else:
        close = pd.DataFrame()
        volume = pd.DataFrame()
    facts: dict[int, list] = {}
    if ids:
        frows = db.execute(select(FundamentalFact.security_id, FundamentalFact.filed_date, FundamentalFact.period_end,
                                  FundamentalFact.metric, FundamentalFact.value)
                           .where(FundamentalFact.security_id.in_(ids), FundamentalFact.fiscal_period == "FY",
                                  FundamentalFact.filed_date.is_not(None))).all()
        for sid, filed, pe, m, v in frows:
            facts.setdefault(sid, []).append((filed, pe, m, v))
        for sid in facts:
            facts[sid].sort(key=lambda r: (r[0], r[1]))
    fx: dict[str, pd.Series] = {}
    ccys = {m.currency for m in metas.values()} - {"USD"}
    for c in ccys:
        any_sample = any(m.is_sample and m.currency == c for m in metas.values())
        real = db.execute(select(FxRate.date, FxRate.rate).where(FxRate.base == "USD", FxRate.quote == c,
                                                                FxRate.source != "sample").order_by(FxRate.date)).all()
        use = real
        if not real and any_sample:
            use = db.execute(select(FxRate.date, FxRate.rate).where(FxRate.base == "USD", FxRate.quote == c,
                                                                   FxRate.source == "sample").order_by(FxRate.date)).all()
        if use:
            fx[c] = pd.Series([1.0 / r[1] for r in use], index=[r[0] for r in use])
    return Panel(metas, close, volume, facts, fx)


def month_ends(dates: list[date], start: date, end: date) -> list[date]:
    out = []
    for i, d in enumerate(dates):
        if d < start or d > end:
            continue
        nxt = dates[i + 1] if i + 1 < len(dates) else None
        if nxt is None or nxt.month != d.month:
            out.append(d)
    return out


def shift_days(d: date, n: int) -> date:
    return d + timedelta(days=n)
