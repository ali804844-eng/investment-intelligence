"""Provider adapters tested against payloads shaped like each vendor's documented responses (mocked transport).

These verify parsing, error handling, secret redaction and routing. They do NOT prove live connectivity;
live verification requires network access and credentials (see docs/DATA_PROVIDERS.md).
"""
from datetime import date

import httpx
import pytest

from app.providers.base import ProviderError, ProviderNotConfigured, ProviderQuotaExceeded, _redact
from app.providers.sec_edgar import SecEdgarProvider, parse_company_facts, parse_submissions
from app.providers.vendors import AlphaVantageProvider, FredProvider, FrankfurterProvider, TiingoProvider, parse_av_daily


def transport(routes: dict):
    def handler(req: httpx.Request):
        for frag, (status, body) in routes.items():
            if frag in str(req.url):
                if isinstance(body, str):
                    return httpx.Response(status, text=body)
                return httpx.Response(status, json=body)
        return httpx.Response(404, json={"detail": "not found"})

    return httpx.MockTransport(handler)


COMPANY_FACTS = {
    "cik": 320193,
    "facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [
            {"start": "2022-01-01", "end": "2022-12-31", "val": 100, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2023-02-01", "accn": "a1"},
            {"start": "2022-10-01", "end": "2022-12-31", "val": 30, "fy": 2022, "fp": "FY", "form": "10-K", "filed": "2023-02-01", "accn": "a1"},
            {"start": "2023-01-01", "end": "2023-12-31", "val": 120, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "a2"},
            {"start": "2023-01-01", "end": "2023-12-31", "val": 121, "fy": 2023, "fp": "FY", "form": "10-K/A", "filed": "2024-05-01", "accn": "a3"},
            {"start": "2023-01-01", "end": "2023-03-31", "val": 25, "fy": 2023, "fp": "Q1", "form": "10-Q", "filed": "2023-05-01", "accn": "q1"},
        ]}},
        "Assets": {"units": {"USD": [{"end": "2023-12-31", "val": 500, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "a2"}]}},
        "StockholdersEquity": {"units": {"USD": [{"end": "2023-12-31", "val": 200, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "a2"}]}},
        "LiabilitiesAndStockholdersEquity": {"units": {"USD": [{"end": "2023-12-31", "val": 500, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "a2"}]}},
        "EarningsPerShareDiluted": {"units": {"USD/shares": [{"start": "2023-01-01", "end": "2023-12-31", "val": 1.5, "fy": 2023, "fp": "FY", "form": "10-K", "filed": "2024-02-01", "accn": "a2"}]}},
    }},
}


def test_sec_company_facts_parsing():
    facts = {(f.metric, f.period_end): f for f in parse_company_facts(COMPANY_FACTS)}
    assert facts[("revenue", date(2022, 12, 31))].value == 100  # quarterly-duration row ignored
    assert facts[("revenue", date(2023, 12, 31))].value == 121  # restatement (later filing) wins
    assert facts[("revenue", date(2023, 12, 31))].filed_date == date(2024, 5, 1)
    assert ("revenue", date(2023, 3, 31)) not in facts  # 10-Q excluded from annual series
    tl = facts[("total_liabilities", date(2023, 12, 31))]
    assert tl.value == 300 and tl.quality == "derived"
    assert facts[("eps_diluted", date(2023, 12, 31))].unit == "USD/shares"


def test_sec_submissions_and_config():
    sub = {"filings": {"recent": {"form": ["10-K", "4", "8-K"], "accessionNumber": ["0000320193-24-000001", "x", "0000320193-24-000002"],
                                  "filingDate": ["2024-11-01", "2024-10-01", "2024-09-01"], "reportDate": ["2024-09-28", "", ""],
                                  "primaryDocument": ["aapl-10k.htm", "", "8k.htm"], "primaryDocDescription": ["10-K", "", "8-K"]}}}
    f = parse_submissions(sub, 320193, 10)
    assert [x.form for x in f] == ["10-K", "8-K"]
    assert f[0].url == "https://www.sec.gov/Archives/edgar/data/320193/000032019324000001/aapl-10k.htm"
    p = SecEdgarProvider(None)
    assert not p.is_configured()
    with pytest.raises(ProviderNotConfigured):
        p.ticker_map()


def test_sec_sends_user_agent_and_resolves_cik():
    seen = {}

    def handler(req):
        seen["ua"] = req.headers.get("user-agent")
        return httpx.Response(200, json={"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}})

    p = SecEdgarProvider("Research Bot ops@example.com", transport=httpx.MockTransport(handler))
    assert p.cik_for("aapl") == "0000320193"
    assert seen["ua"] == "Research Bot ops@example.com"


def test_tiingo_bars_and_errors():
    rows = [{"date": "2024-01-02T00:00:00.000Z", "close": 185.6, "high": 188.4, "low": 183.9, "open": 187.2, "volume": 82488700,
             "adjClose": 184.3, "divCash": 0.0, "splitFactor": 1.0},
            {"date": "2024-01-03T00:00:00.000Z", "close": 184.2, "high": 185.9, "low": 183.4, "open": 184.2, "volume": 58414500,
             "adjClose": 182.9, "divCash": 0.24, "splitFactor": 1.0}]
    p = TiingoProvider("k", transport=transport({"/prices": (200, rows)}))
    bars = p.daily_bars("AAPL", date(2024, 1, 1), date(2024, 1, 5))
    assert bars[0].date == date(2024, 1, 2) and bars[1].dividend == 0.24 and bars[0].adj_close == 184.3
    p2 = TiingoProvider("k", transport=transport({"/prices": (429, {"detail": "rate"})}))
    with pytest.raises(ProviderQuotaExceeded):
        p2.daily_bars("AAPL", date(2024, 1, 1), date(2024, 1, 5))
    assert not TiingoProvider(None).is_configured()


def test_alphavantage_rate_limit_note_and_parse():
    p = AlphaVantageProvider("k", transport=transport({"query": (200, {"Information": "Thank you for using Alpha Vantage! rate limit"})}))
    p.min_interval_seconds = 0
    with pytest.raises(ProviderQuotaExceeded):
        p.daily_bars("IBM", date(2024, 1, 1), date(2024, 2, 1))
    data = {"Meta Data": {}, "Time Series (Daily)": {"2024-01-03": {"1. open": "1", "2. high": "2", "3. low": "0.5", "4. close": "1.5", "5. volume": "10"},
                                                      "2024-01-02": {"1. open": "1", "2. high": "2", "3. low": "0.5", "4. close": "1.2", "5. volume": "10"}}}
    bars = parse_av_daily(data, date(2024, 1, 1), date(2024, 1, 31))
    assert [b.close for b in bars] == [1.2, 1.5]
    with pytest.raises(ProviderError):
        parse_av_daily({"Error Message": "bad"}, date(2024, 1, 1), date(2024, 1, 2))


def test_fred_missing_values_and_frankfurter():
    p = FredProvider("k", transport=transport({"observations": (200, {"observations": [
        {"date": "2024-01-01", "value": "."}, {"date": "2024-01-02", "value": "3.95"}]})}))
    p.min_interval_seconds = 0
    assert p.macro_series("DGS10", date(2024, 1, 1)) == [(date(2024, 1, 2), 3.95)]
    f = FrankfurterProvider(transport=transport({"..": (200, {"base": "USD", "rates": {"2024-01-02": {"EUR": 0.91, "JPY": 142.1},
                                                                                       "2024-01-03": {"EUR": 0.915, "JPY": 143.0}}})}))
    f.min_interval_seconds = 0
    s = f.fx_series("USD", ["EUR", "JPY"], date(2024, 1, 1), date(2024, 1, 3))
    assert s["EUR"][1] == (date(2024, 1, 3), 0.915)


def test_secrets_redacted_and_non_json():
    assert "secret123" not in _redact("https://x.test/q?symbol=A&apikey=secret123&token=abc")
    p = AlphaVantageProvider("secret123", transport=transport({"query": (500, "oops")}))
    p.min_interval_seconds = 0
    with pytest.raises(ProviderError) as e:
        p.daily_bars("IBM", date(2024, 1, 1), date(2024, 2, 1))
    assert "secret123" not in str(e.value)


def test_registry_routing_isolates_sample():
    from app.config import Settings
    from app.providers.registry import Registry

    reg = Registry(Settings(tiingo_api_key="k", sec_user_agent="a b@c.d", enable_sample_data=True))
    assert [p.name for p in reg.for_capability("prices", sample=True)] == ["sample"]
    assert [p.name for p in reg.for_capability("prices", sample=False)] == ["tiingo"]
    assert "sample" not in [p.name for p in reg.for_capability("fundamentals", sample=False)]
    reg2 = Registry(Settings(enable_sample_data=False))
    assert reg2.for_capability("prices", sample=True) == []
    assert reg2.for_capability("prices", sample=False) == []
