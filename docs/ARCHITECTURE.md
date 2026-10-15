# Architecture

```
 Browser (React SPA)  ──HTTPS/cookie+CSRF header──▶  FastAPI (app.main)  ──▶ PostgreSQL
                                                         │   ▲                 ▲
                                                         │   │ same services   │ SKIP LOCKED queue,
                                                         ▼   │                 │ schedules, heartbeats
                                          Anthropic API ◀── agent (tool loop)  │
                                                                               │
                                     Worker process (app.worker.runner) ───────┘
                                        scheduler tick → enqueue due jobs
                                        claim job → handler → log / retry / notify
                                        provider adapters → SEC, Tiingo, Alpha Vantage, FRED, Frankfurter, SAMPLE
```

## Backend layout (`backend/app`)

| Path | Responsibility |
|---|---|
| `config.py` | Environment-based settings (pydantic-settings); secrets as `SecretStr` |
| `models/` | SQLAlchemy models (37 tables). Every market datum stores `source` + `quality`; money uses `Numeric` |
| `providers/` | Vendor adapters → neutral dataclasses. `registry.py` routes by capability; SAMPLE instruments are served only by the sample provider, and real ones never are |
| `services/market_data.py` | Fetch, persist (upserts), freshness, FX conversion, provider health and usage accounting |
| `services/panel.py` | Point-in-time feature engine shared by live screens and backtests |
| `core/finance/` | Pure, tested maths: accounting (FIFO/AVG lots, FX-aware realized P&L), returns (TWR, XIRR), risk metrics, indicators, valuation (DCF, WACC, comps, sensitivity, scenarios), ratios and the safe formula evaluator |
| `core/risk/engine.py` | Deterministic limit checks, pre-trade checks, stress tests, position sizing. No LLM in the path |
| `core/screening/` | Strategy definitions, criteria evaluation, z-score ranking, explanations, short-term setups |
| `core/backtest/engine.py` | Monthly cross-sectional backtester (next-day execution, costs), walk-forward, sensitivity |
| `core/evaluation/scoring.py` | Prediction scoring (Brier, log loss), calibration, experiment acceptance |
| `services/research.py` | Overview, auto-valuation with assumption basis, 14-section reports, report → prediction registration |
| `services/portfolio.py` | Portfolio state, daily history (TWR/MWR), exposures, correlations, risk report, rebalancing, snapshots |
| `services/learning.py` | Prediction registry, strategy versions, backtest jobs, experiments, promote/reject/rollback |
| `services/alerts.py`, `notifications.py` | Alert rules with cooldowns; notifications with de-dupe and optional SMTP |
| `agent/` | `tools.py` (27 permission-scoped tools), `llm.py` (Claude manual tool loop, budgets, usage), `planner.py` (deterministic fallback), `chat.py` (sessions, append-only history), `safety.py` (untrusted-text envelope) |
| `worker/` | `queue.py` (enqueue, claim, retry with backoff, cancel, stale recovery, cron), `jobs.py` (handlers incl. the 12-step research cycle), `runner.py` (process loop, advisory-locked scheduler, heartbeats) |
| `api/` | Routers; every query is scoped to the authenticated user |

## Key design decisions

- **One feature engine for live and historical use.** `Panel.features(sid, as_of)` only sees prices up to `as_of`
  and fundamentals *filed* on or before `as_of`. Backtests therefore cannot use information that was not yet
  public, and the live screen behaves exactly like the backtested one.
- **The database is the queue.** PostgreSQL `FOR UPDATE SKIP LOCKED` lets several workers run safely without
  Redis or Celery. The scheduler tick runs under `pg_try_advisory_xact_lock`, so only one worker enqueues
  scheduled jobs. Jobs write their step log through a separate session, so a job's own transaction never locks
  its row. Timeouts and cancellation are cooperative (checked at every logged step).
- **Manual LLM loop.** The loop owns persistence, budgets, permission scoping, parallel tool results and error
  fallbacks. Conversation history is replayed append-only. If a replay is rejected, the loop retries once with
  text-only history. Server-side refusal fallbacks are enabled (`fallbacks="default"`).
- **Explain, don't just score.** Screens return criteria passed/failed/missing, risk flags, invalidation and
  missing data. Reports carry sources and calculation records. "Insufficient evidence" is a first-class
  conclusion.
- **Controlled learning.** Strategy parameters are data (versioned rows). Candidates are tested on the same
  walk-forward windows as production, against acceptance criteria stored before the test runs. Promotion needs an
  authenticated, audited user action.

## Main flows

1. **Research cycle (worker):** priorities → data refresh (budgeted) → validation (stale/missing prices, provider
   health) → change detection (filings, large moves) → all strategy screens → reports for the top N (bear cases,
   risks, invalidation) → portfolio exposure checks → store the run and candidates → ranked queue → notify only on
   material findings → evaluate due predictions.
2. **Chat:** the user message plus a context block (date, portfolios, watchlists, stored preferences) goes to
   Claude with the tools. Each tool call runs in a DB savepoint. Proposals become `PendingAction` rows and are
   shown as approval cards.
3. **Approval:** the user confirms, the risk engine re-runs against current prices, and the paper transaction is
   written with `origin=agent_paper`. The decision is audited.

## Frontend layout (`frontend/src`)

`App.tsx` (auth gate, shell, lazy routes), `api.ts` (fetch wrapper adding the CSRF header), `components/ui.tsx`
(cards, quality badges, tables, tabs), `components/charts.tsx` (validated colour palette, line/candlestick/heatmap/
calibration charts), one page per module in `pages/`.
