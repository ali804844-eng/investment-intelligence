# Operating manual

## Processes

| Process | Command | Notes |
|---|---|---|
| API + UI | `uvicorn app.main:app` | Stateless; scale horizontally behind a proxy (the rate limiter is per-instance) |
| Worker | `python -m app.worker.runner` | **Must be running for continuous research.** Several may run concurrently |
| Migrations | `alembic upgrade head` | Run before starting a new version |

The dashboard and Agent Control show **"Agent worker running"** only if a worker heart-beat in the last 2 minutes.
That signal comes from the database, so the UI never claims continuous operation without evidence.

## Default schedules (UTC, editable in Agent Control → Schedules)

| Job | Cron | Purpose |
|---|---|---|
| Daily market review | `15 22 * * 1-5` | Benchmarks/macro refresh, regime, large moves |
| Watchlist & portfolio review | `30 22 * * 1-5` | Refresh holdings, evaluate alerts, risk limits, snapshots |
| Daily research cycle | `0 23 * * 1-5` | Full 12-step cycle (screens, reports, ranked queue) |
| Filings & events monitor | `0 */6 * * *` | New filings, earnings/dividend dates, news |
| Prediction evaluation | `0 6 * * *` | Score predictions whose window ended |
| Weekly performance review | `0 8 * * 6` | Snapshots + scorecard notification |
| Weekly strategy validation | `0 10 * * 0` | Walk-forward per strategy; weak ones get a tested candidate |
| Data health check | `*/30 * * * *` | Degraded providers, stale watched prices |

Controls: **Pause** stops scheduled jobs from being enqueued, and queued scheduled jobs wait. **Stop** also
cancels queued scheduled jobs. Manual jobs still run in both states. Operating hours and weekday-only settings skip
scheduled runs and record why. Budgets: the daily/monthly AI spend caps are hard stops (the agent falls back to
deterministic mode), and so is the daily data-provider call budget.

## Failures and retries

- A failing job is retried with exponential backoff (30s, 60s, …, max 1h) up to `max_attempts` (default 3). Each
  attempt's error is kept in `error_history`.
- After the final failure, an in-app notification is raised (de-duplicated for 6 hours).
- If a worker dies mid-job, its heartbeat stops. After `WORKER_STALE_AFTER_SECONDS` another worker re-queues the
  job.
- Inspect any job in Agent Control → Jobs (step log, data, traceback, retry history). Use **Retry** or **Cancel**
  there.

## Backup and recovery

- `deploy/backup.sh` writes a compressed `pg_dump` custom-format archive, verifies it, and keeps 14 days. Schedule
  it nightly (cron or systemd timer). With Docker:
  `docker compose exec -T db pg_dump -U ii -Fc ii > ii-$(date +%F).dump`.
- Restore into an empty database:
  `pg_restore --clean --if-exists --no-owner --dbname=postgresql://ii:…@host/ii file.dump`, then run
  `alembic upgrade head` and start the API and worker. (The dump/restore cycle was verified during the build.)
- Copy backups off-host. They contain portfolio and transaction data, so treat them as sensitive and encrypt them
  at rest.
- Also keep `.env` in a secrets manager. Losing `SECRET_KEY` only logs users out.

## Upgrades

`git pull` → `make install` → `make migrate` → `make build` → restart the API and worker
(or `docker compose up -d --build`).

## Monitoring

- `GET /api/health` reports database status and worker liveness (point an uptime monitor at it).
- The API emits structured JSON logs with a request id, path, status and duration. Every response carries
  `X-Request-ID`.
- Provider health, error messages and call counts appear in Agent Control → Status.
- Optional: ship logs to your aggregator, and alert on `worker_alive=false` or a growing count of failed jobs.

## Costs (estimates)

| Item | Cost |
|---|---|
| Hosting (1 small VM: 2 vCPU / 4 GB, Postgres on the same box) | ~$10–40/month |
| SEC EDGAR, FRED, Frankfurter | Free |
| Tiingo | Free tier, or ~$30/month for higher limits |
| Alpha Vantage | Free tier (≈25 calls/day), or ~$50+/month |
| Claude (Opus 5.5: $4 input / $20 output per 1M tokens) | A typical chat turn with 3–6 tool calls costs ~$0.03–0.15. With the default caps ($2/day, $30/month) spend is bounded. Report narratives are optional |
