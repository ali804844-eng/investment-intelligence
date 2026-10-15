# Security model

| Concern | Control |
|---|---|
| Authentication | argon2id password hashes; signed (HS256) session token in an `httpOnly`, `SameSite=Lax` cookie (`Secure` in production); 5 failed logins → 15-minute lockout; timing-equalised unknown-user path |
| CSRF | Cookie-authenticated mutations require `X-Requested-With: ii` (cross-site forms cannot set it without a CORS preflight) |
| Authorization | Every query is filtered by `user_id`; foreign IDs return 404 (tested) |
| Rate limiting | Per-IP sliding window on login, register, chat, screens, reports, backtests and refreshes (per process; add proxy-level limits for multi-instance deployments) |
| Input validation | Pydantic schemas with bounds on every endpoint; the formula evaluator is an AST whitelist (no attribute access, calls limited to abs/min/max/sqrt/log); CSV import is size-capped and all-or-nothing |
| Secrets | Env-only (`SecretStr`); never returned by any endpoint (tested), never stored, redacted from URLs in errors; `.env` is git-ignored |
| Headers | CSP (`default-src 'self'`), `X-Frame-Options: DENY`, `nosniff`, `Referrer-Policy`, `Permissions-Policy`, `Cache-Control: no-store` on the API; the API docs are disabled in production |
| Prompt injection | External text is wrapped as `untrusted` with provenance and pattern flags, and tags are neutralised. The system prompt treats it as data only. Tools are permission-scoped in code: no tool can trade, change limits or change permissions. Proposals require explicit user confirmation and a risk re-check. Model output is rendered without raw HTML |
| Financial integrity | Decimal ledger arithmetic; the full ledger is replayed on every write (oversells and inconsistent deletions are rejected); soft-deleted transactions; SAMPLE and real instruments cannot be mixed; stale prices are rejected pre-trade |
| Audit | `audit_logs` records registrations, logins, failed logins, portfolio/transaction/watchlist/alert/schedule changes, risk-limit before/after, agent proposals and decisions, strategy promotions/rollbacks, and job actions. The actor is user, agent or worker |
| Live trading | Not implemented. The `live` portfolio kind is rejected at creation, and the risk engine's live-trading gate blocks any order. Adding brokerage execution would require the controls listed in the product requirements (pre-trade check, order preview, buying-power verification, duplicate protection, reconciliation, kill switch) plus separate confirmation |
| Production guard | The API refuses to start with `APP_ENV=production` and the default `SECRET_KEY` |

Report vulnerabilities privately to the repository owner.
