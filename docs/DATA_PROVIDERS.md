# Data providers

## Comparison (evaluated October 2026)

Third-party plan limits change often; confirm on each vendor's site before relying on them.

| Provider | Coverage | History | Frequency | Fundamentals | News/filings | Cost & limits | Licensing notes | Status here |
|---|---|---|---|---|---|---|---|---|
| **SEC EDGAR** (official) | US SEC filers | Full XBRL history (~2009+) | Filing-driven | **As reported** (10-K/20-F facts) | Filing index | Free; ≤10 req/s; declared User-Agent required | Public data; fair-access policy | Adapter + parser tests. Needs `SEC_USER_AGENT` and network access |
| **Tiingo** | US equities/ETFs (+ some intl) | Decades of EOD | End-of-day | Paid tiers | — | Free tier with hourly/monthly symbol limits; paid from ~$30/mo | Free tier for personal use | Adapter (prices, dividends, splits). Needs `TIINGO_API_KEY` |
| **Alpha Vantage** | Global equities, FX, crypto | 20y daily | EOD on free tier | Overview only | News & sentiment, earnings calendar | Free ≈25 calls/day; paid tiers per minute | Free tier non-commercial | Adapter (daily prices, news, earnings). Needs `ALPHAVANTAGE_API_KEY`; daily cap enforced (25) |
| **FRED** | US/intl macro & rates | Decades | Series-dependent | — | — | Free key | Respect series copyright notes | Adapter (rates, VIX, HY spread, unemployment, CPI). Needs `FRED_API_KEY` |
| **Frankfurter** (ECB reference) | ~30 currencies | 1999+ | Daily ~16:00 CET | — | — | Free, no key | Open source over ECB data | Adapter, on by default. **Blocked by this build sandbox's network policy** |
| Stooq | Global EOD | Long | EOD | — | — | Reportedly requires a key since 2026 | Unclear terms | Not implemented |
| SAMPLE (built-in) | 47 fictional instruments | 2015–today | EOD | Synthetic statements | Synthetic | — | Generated locally | Active. Always labelled SAMPLE |

**Why these:** official filings (SEC) are the source of truth for reported financials. Tiingo gives clean,
corporate-action-aware EOD prices at low cost. Alpha Vantage adds news and earnings dates. FRED and ECB are the
canonical public macro and FX sources. Adapters are isolated in `backend/app/providers/`, so any vendor can be
replaced by implementing the same methods (e.g. Polygon, EODHD or FMP for broader international fundamentals or
real-time data) without touching the rest of the system.

**Not covered without a licensed vendor:** real-time or intraday quotes, analyst estimates, non-US
fundamentals, bond-level fixed-income data, and corporate-event calendars beyond Alpha Vantage earnings dates.
The UI labels these as unavailable rather than approximating them.

## Setup steps

1. **SEC EDGAR:** set `SEC_USER_AGENT="Your Name your.email@example.com"`. No account needed.
2. **Tiingo:** sign up at tiingo.com → Account → API → copy the token → `TIINGO_API_KEY=…`.
3. **Alpha Vantage:** alphavantage.co/support/#api-key → `ALPHAVANTAGE_API_KEY=…`.
4. **FRED:** create an account at fredaccount.stlouisfed.org → API Keys → `FRED_API_KEY=…`.
5. **Anthropic (agent):** console.anthropic.com → API keys → `ANTHROPIC_API_KEY=…`. Set daily/monthly AI budgets
   in Agent Control → Hours & budgets.
6. Restart the API and worker. Then run `python -m app.cli check-providers`. Each configured provider is called
   once and reports OK or the exact error.
7. Add real securities in **Markets → Add security** (e.g. `AAPL`, `SPY`). The worker keeps tracked securities
   (watchlists, holdings, benchmarks) refreshed. Once real equities exist, screens and backtests use the real
   universe automatically. Set `ENABLE_SAMPLE_DATA=false` to hide the sample universe entirely.

## Data handling rules (enforced in code)

- Every row stores `source`, `quality`, and fetch time. Fundamentals store the XBRL tag, accession and filing
  date. Restatements: the latest filing wins.
- Non-positive prices are rejected. Missing provider values stay missing; they are never filled with estimates.
  FRED's `.` placeholders are dropped.
- Freshness: quotes older than the configured business-day threshold are labelled STALE. Stale prices never
  trigger price alerts and fail pre-trade checks.
- Provider calls are throttled per vendor, counted per day (`api_usage`), capped by vendor and user budgets, and
  failures mark the provider *degraded* (visible in the dashboard, Agent Control, and the `data_feed` alert).
