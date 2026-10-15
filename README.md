# Investment Intelligence — AI Portfolio Command Center

A personal investment research and portfolio-intelligence platform with a persistent, tool-using AI research
agent. It combines a research terminal, portfolio analytics, a quantitative research lab, and an agent that runs
scheduled research cycles in the background. The agent keeps memory of past research and is evaluated against its
own recorded predictions.

> **Research tool, not investment advice.** Live order execution is intentionally not implemented. All valuations
> and forecasts are estimates under stated assumptions, and simulated performance is labelled as hypothetical.

## What's inside

| Module | What it does |
|---|---|
| Executive dashboard | Value, P&L, TWR vs benchmark, risk snapshot, exposures, regime, watchlist, research queue, events, agent status, data freshness |
| AI agent | Chat workspace. Claude runs a tool-use loop over 27 typed tools (data, statements, calculations, valuation, screens, portfolio, risk, memory, reports, predictions, alerts, jobs, proposals). Without an API key it falls back to a deterministic mode that runs the same tools |
| Global markets | Indices, ETFs, bonds, commodities, FX, digital assets (research only), macro series, filterable securities explorer |
| Discovery engine | Six versioned strategies (quality, value, growth, momentum, income, defensive) plus a custom criteria builder. Each candidate is explained: why selected, criteria passed/failed/missing, metrics, risks, invalidation, missing data, further research |
| Long-term research | Statements with filing provenance, derived ratios, consistency checks, DCF with WACC, sensitivity grid, scenarios, comparables, margin of safety |
| Short-term research | Breakout, pullback, and oversold-reversal setups with entry, invalidation, exits, R/R, costs, event risk, and a per-instrument historical event study |
| Portfolio studio | Multiple portfolios (long-term, trading, research, paper, manual), FIFO/average cost, multi-currency, dividends/fees/splits, CSV import, TWR and MWR, benchmark comparison, rebalancing simulation, stress tests, sizing, pre-trade checks, audit trail |
| Research reports | A standardized 14-section report with sources and calculations. Conclusions are *consider / monitor / reject / insufficient evidence* |
| Financial lab | Multi-company statement and ratio comparison, safe user-defined formulas, CSV export |
| Learning lab | Prediction registry, Brier score and calibration, point-in-time backtests with costs, walk-forward, sensitivity, candidate strategy versions, experiments, human-approved promotion and rollback |
| Alerts | 13 alert kinds (price, % move, valuation, filings, earnings, dividends, news, thesis change, concentration, drawdown, data feed, research tasks) with cooldowns and de-duplication; in-app plus optional email |
| Agent control center | State (run / pause / stop), worker heartbeat, jobs with logs, retry and cancel, cron schedules, provider health, API usage and cost, budgets, operating hours, proposals, memory |

## Architecture (short)

`frontend/` React + TypeScript + Vite + Tailwind, TanStack Query, Recharts, TradingView lightweight-charts.
`backend/` FastAPI + SQLAlchemy 2 + Alembic on PostgreSQL. Financial maths lives in deterministic Python modules
(`app/core/…`). The worker (`python -m app.worker.runner`) is a separate process: a cron scheduler plus a
Postgres-backed job queue (`SKIP LOCKED`) with retries, timeouts, heartbeats and stale-job recovery.
See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

## Quick start (local, no Docker)

Requirements: Python ≥ 3.11, Node ≥ 20, PostgreSQL ≥ 14, and [uv](https://docs.astral.sh/uv/) (or pip).

```bash
# 1. database
createuser -P ii            # choose a password
createdb -O ii ii

# 2. configuration
cp .env.example backend/.env
#   set DATABASE_URL=postgresql+psycopg://ii:<password>@localhost:5432/ii
#   set SECRET_KEY, APP_ENV=development, COOKIE_SECURE=false, CORS_ORIGINS=http://localhost:5173

# 3. install, migrate, build the UI
make install
make migrate
make build               # builds the SPA into backend/static (served by the API)

# 4. run (two processes)
make api                 # http://127.0.0.1:8000
make worker              # background research agent - REQUIRED for continuous operation
```

Open http://127.0.0.1:8000 and create an account. Leave "Load the SAMPLE universe" ticked to explore every module
before any data provider is configured; SAMPLE data is fictional and labelled everywhere. For frontend development,
run `make web` (Vite on :5173, proxying `/api` to :8000).

## Deploy with Docker Compose

```bash
cp .env.example .env     # set SECRET_KEY, POSTGRES_PASSWORD, providers…
docker compose up -d --build
docker compose logs -f worker
```

Compose runs `db`, a one-shot `migrate`, `api` (bound to 127.0.0.1:8000), and `worker`. Put a TLS reverse proxy in
front (see `deploy/Caddyfile`) and keep `COOKIE_SECURE=true`. systemd units for a non-Docker host are in `deploy/`.
Backups: `deploy/backup.sh` (tested dump and restore). See [docs/OPERATIONS.md](docs/OPERATIONS.md).

## Configuration

All configuration comes from environment variables (see [`.env.example`](.env.example)). Secrets stay on the
server: they are never sent to the browser, stored in the database, or written to logs, and query-string keys are
redacted from error messages.

| Variable | Needed for |
|---|---|
| `SECRET_KEY` | Session signing (required in production; the API refuses to start with the dev default) |
| `ANTHROPIC_API_KEY` | Conversational agent (Claude, model `claude-opus-5-5` by default) and optional report narratives |
| `SEC_USER_AGENT` | SEC EDGAR fundamentals and filings for US companies (free) |
| `TIINGO_API_KEY` | End-of-day prices for real securities |
| `ALPHAVANTAGE_API_KEY` | Price fallback, news and sentiment, earnings calendar |
| `FRED_API_KEY` | Macro and rates |
| `ENABLE_FRANKFURTER` | ECB reference FX (no key) |
| `SMTP_*` | Email alerts |

Provider comparison and setup steps: [docs/DATA_PROVIDERS.md](docs/DATA_PROVIDERS.md).
Run `python -m app.cli check-providers` from `backend/` to call each configured provider once and report status.

## Tests

```bash
make test     # backend: unit, integration (PostgreSQL), provider parsing, agent loop, bias controls
make e2e      # browser tests against a running API + worker (Playwright)
```

The backend tests need a PostgreSQL database for integration runs; the default is
`postgresql+psycopg://ii:ii_dev_password@localhost:5432/ii_test` (override with `TEST_DATABASE_URL`).
**The test suite drops and recreates the `public` schema of that database.**

## Safety and integrity

- Research and paper trading only. `live` portfolios are rejected, and the risk engine's live-trading gate blocks
  any order.
- The agent can only *propose* watchlist or paper-trade changes. You approve them with an explicit confirmation,
  and the independent risk engine re-checks them at approval time. Duplicate execution is blocked.
- The risk engine is plain code with no LLM in the path. The agent has no tool to change limits, code or
  permissions.
- External text (news, filings) is wrapped as untrusted data, and instruction-like content is flagged.
- Every value carries a source, a timestamp and a quality label (`live / delayed / eod / reported / reference /
  derived / manual / sample`). Stale data is flagged and rejected by pre-trade checks.
- Material actions are audit-logged (Settings → Audit log).

See [docs/SECURITY.md](docs/SECURITY.md).
