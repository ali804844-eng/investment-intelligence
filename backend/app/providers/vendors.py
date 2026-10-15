"""Commercial / public market-data adapters: Tiingo (EOD prices), Alpha Vantage (prices, news), FRED (macro),
Frankfurter (ECB reference FX). Response formats follow each vendor's public documentation."""
from __future__ import annotations

import csv
import io
from datetime import date, datetime, timezone

from app.providers.base import (
    Bar,
    EventInfo,
    NewsInfo,
    Provider,
    ProviderError,
    ProviderQuotaExceeded,
    SecurityInfo,
    parse_date,
)


class TiingoProvider(Provider):
    name = "tiingo"
    title = "Tiingo (end-of-day prices, corporate actions)"
    capabilities = {"search", "reference", "prices", "events"}
    quality = "eod"
    min_interval_seconds = 0.5
    homepage = "https://www.tiingo.com/documentation/end-of-day"
    licensing = "Free tier for personal use with hourly/monthly symbol limits; commercial use requires a paid plan."
    setup = "Create a free account at tiingo.com, copy the API token, set TIINGO_API_KEY."

    def __init__(self, api_key: str | None, **kw):
        super().__init__(**kw)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _h(self) -> dict[str, str]:
        return {"Authorization": f"Token {self.api_key}", "Content-Type": "application/json"}

    def search(self, query: str) -> list[SecurityInfo]:
        rows = self._get("https://api.tiingo.com/tiingo/utilities/search", {"query": query}, self._h())
        out = []
        for r in rows or []:
            ac = {"Stock": "equity", "ETF": "etf", "Mutual Fund": "fund"}.get(r.get("assetType"), "equity")
            out.append(SecurityInfo(r["ticker"].upper(), r.get("name") or r["ticker"], "", ac,
                                    (r.get("countryCode") or "US")[:2], "USD"))
        return out

    def security_info(self, symbol: str) -> SecurityInfo | None:
        r = self._get(f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}", headers=self._h())
        if not r or "ticker" not in r:
            return None
        return SecurityInfo(r["ticker"].upper(), r.get("name") or symbol, r.get("exchangeCode") or "", "equity",
                            "US", "USD", description=r.get("description"))

    def daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        rows = self._get(f"https://api.tiingo.com/tiingo/daily/{symbol.lower()}/prices",
                         {"startDate": start.isoformat(), "endDate": end.isoformat()}, self._h())
        if isinstance(rows, dict) and rows.get("detail"):
            raise ProviderError(f"tiingo: {rows['detail']}")
        return [parse_tiingo_bar(r) for r in rows or []]

    def events(self, symbol: str) -> list[EventInfo]:
        # Corporate actions are embedded in the price rows (divCash, splitFactor); handled by the service.
        return []


def parse_tiingo_bar(r: dict) -> Bar:
    return Bar(
        date=parse_date(r["date"]), close=float(r["close"]), open=_f(r.get("open")), high=_f(r.get("high")),
        low=_f(r.get("low")), adj_close=_f(r.get("adjClose")), volume=_f(r.get("volume")),
        dividend=_f(r.get("divCash")) or None, split_factor=_f(r.get("splitFactor")),
    )


def _f(v) -> float | None:
    try:
        return None if v is None or v == "" else float(v)
    except (TypeError, ValueError):
        return None


class AlphaVantageProvider(Provider):
    name = "alphavantage"
    title = "Alpha Vantage (daily prices, news & sentiment)"
    capabilities = {"search", "prices", "news", "events"}
    quality = "eod"
    daily_limit = 25
    min_interval_seconds = 12.5  # free tier: 5 requests/minute
    homepage = "https://www.alphavantage.co/documentation/"
    licensing = "Free key for non-commercial use with tight daily limits; premium plans for higher volume."
    setup = "Get a free key at alphavantage.co/support/#api-key and set ALPHAVANTAGE_API_KEY."

    def __init__(self, api_key: str | None, **kw):
        super().__init__(**kw)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def _q(self, params: dict, expect: str = "json"):
        data = self._get("https://www.alphavantage.co/query", {**params, "apikey": self.api_key}, expect=expect)
        if isinstance(data, dict):
            for k in ("Note", "Information"):
                if k in data and len(data) <= 2:
                    raise ProviderQuotaExceeded(f"alphavantage: {data[k][:160]}")
            if "Error Message" in data:
                raise ProviderError(f"alphavantage: {data['Error Message'][:160]}")
        return data

    def search(self, query: str) -> list[SecurityInfo]:
        data = self._q({"function": "SYMBOL_SEARCH", "keywords": query})
        out = []
        for m in data.get("bestMatches", []):
            region = m.get("4. region", "")
            out.append(SecurityInfo(m["1. symbol"], m.get("2. name", ""), "", "etf" if m.get("3. type") == "ETF" else "equity",
                                    "US" if region == "United States" else None, m.get("8. currency", "USD")))
        return out

    def daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        size = "full" if (end - start).days > 140 else "compact"
        data = self._q({"function": "TIME_SERIES_DAILY", "symbol": symbol, "outputsize": size})
        return parse_av_daily(data, start, end)

    def news(self, symbol: str, limit: int = 20) -> list[NewsInfo]:
        data = self._q({"function": "NEWS_SENTIMENT", "tickers": symbol, "limit": str(limit)})
        return parse_av_news(data, symbol)[:limit]

    def events(self, symbol: str) -> list[EventInfo]:
        text = self._q({"function": "EARNINGS_CALENDAR", "symbol": symbol, "horizon": "3month"}, expect="text")
        out = []
        for r in csv.DictReader(io.StringIO(text)):
            if r.get("reportDate"):
                out.append(EventInfo("earnings", parse_date(r["reportDate"]),
                                     {"fiscal_date_ending": r.get("fiscalDateEnding"), "estimate": r.get("estimate")}))
        return out


def parse_av_daily(data: dict, start: date, end: date) -> list[Bar]:
    series = data.get("Time Series (Daily)")
    if not isinstance(series, dict):
        raise ProviderError("alphavantage: missing 'Time Series (Daily)'")
    out = []
    for ds, r in series.items():
        d = parse_date(ds)
        if start <= d <= end:
            out.append(Bar(d, float(r["4. close"]), _f(r.get("1. open")), _f(r.get("2. high")), _f(r.get("3. low")),
                           None, _f(r.get("5. volume"))))
    return sorted(out, key=lambda b: b.date)


def parse_av_news(data: dict, symbol: str) -> list[NewsInfo]:
    out = []
    for it in data.get("feed", []):
        try:
            ts = datetime.strptime(it["time_published"], "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
        except (KeyError, ValueError):
            continue
        sent = None
        for ts_ in it.get("ticker_sentiment", []):
            if ts_.get("ticker") == symbol:
                sent = _f(ts_.get("ticker_sentiment_score"))
        out.append(NewsInfo(it.get("title", "")[:500], it.get("url", ""), ts, it.get("source"),
                            (it.get("summary") or "")[:2000], sent if sent is not None else _f(it.get("overall_sentiment_score")),
                            [symbol]))
    return out


class FredProvider(Provider):
    name = "fred"
    title = "FRED, Federal Reserve Bank of St. Louis (macro & rates)"
    capabilities = {"macro"}
    quality = "reported"
    min_interval_seconds = 0.6
    homepage = "https://fred.stlouisfed.org/docs/api/fred/"
    licensing = "Free API key; respect series-level copyright notes for redistribution."
    setup = "Request a free key at fredaccount.stlouisfed.org/apikeys and set FRED_API_KEY."

    SERIES = {
        "DGS10": "10-Year Treasury yield (%)", "DGS2": "2-Year Treasury yield (%)", "FEDFUNDS": "Fed funds rate (%)",
        "UNRATE": "Unemployment rate (%)", "CPIAUCSL": "CPI index", "VIXCLS": "CBOE VIX",
        "BAMLH0A0HYM2": "ICE BofA US High Yield OAS (%)", "T10Y2Y": "10Y-2Y spread (%)",
    }

    def __init__(self, api_key: str | None, **kw):
        super().__init__(**kw)
        self.api_key = api_key

    def is_configured(self) -> bool:
        return bool(self.api_key)

    def macro_series(self, series_id: str, start: date) -> list[tuple[date, float]]:
        data = self._get("https://api.stlouisfed.org/fred/series/observations",
                         {"series_id": series_id, "api_key": self.api_key, "file_type": "json",
                          "observation_start": start.isoformat()})
        return parse_fred(data)


def parse_fred(data: dict) -> list[tuple[date, float]]:
    if "observations" not in data:
        raise ProviderError(f"fred: {data.get('error_message', 'missing observations')}")
    out = []
    for o in data["observations"]:
        if o.get("value") in (None, ".", ""):
            continue  # FRED uses '.' for missing observations
        out.append((parse_date(o["date"]), float(o["value"])))
    return out


class FrankfurterProvider(Provider):
    name = "frankfurter"
    title = "Frankfurter (ECB euro foreign-exchange reference rates)"
    capabilities = {"fx"}
    quality = "reference"
    requires_key = False
    min_interval_seconds = 0.3
    homepage = "https://frankfurter.dev"
    licensing = "Open source API over ECB reference rates (published ~16:00 CET on TARGET business days)."
    setup = "No key needed. Requires outbound access to api.frankfurter.dev. Toggle with ENABLE_FRANKFURTER."

    def __init__(self, enabled: bool = True, base_url: str = "https://api.frankfurter.dev/v1", **kw):
        super().__init__(**kw)
        self.enabled = enabled
        self.base_url = base_url

    def is_configured(self) -> bool:
        return self.enabled

    def fx_series(self, base: str, quotes: list[str], start: date, end: date) -> dict[str, list[tuple[date, float]]]:
        data = self._get(f"{self.base_url}/{start.isoformat()}..{end.isoformat()}",
                         {"base": base, "symbols": ",".join(quotes)})
        return parse_frankfurter(data)


def parse_frankfurter(data: dict) -> dict[str, list[tuple[date, float]]]:
    rates = data.get("rates")
    if not isinstance(rates, dict):
        raise ProviderError("frankfurter: missing rates")
    out: dict[str, list[tuple[date, float]]] = {}
    for ds, row in sorted(rates.items()):
        for q, v in row.items():
            out.setdefault(q, []).append((parse_date(ds), float(v)))
    return out
