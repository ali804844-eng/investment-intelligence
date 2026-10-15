"""Database-backed job queue with retries, timeouts, heartbeats and stale-job recovery.

PostgreSQL ``SELECT ... FOR UPDATE SKIP LOCKED`` lets several worker processes claim jobs safely without a
separate broker. Failed attempts are retried with exponential backoff up to ``max_attempts``.
"""
from __future__ import annotations

from datetime import datetime, timedelta

from croniter import croniter
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import JobRun, ScheduledJob

JOB_TYPES = {
    "research_cycle": "Full research cycle: refresh data, validate, detect changes, screen, analyse, rank, notify",
    "market_review": "Refresh market data for indices/benchmarks, update regime, detect large moves",
    "watchlist_review": "Refresh and review watchlist & portfolio holdings; evaluate alerts and risk limits",
    "screening": "Run active strategy screens and update the ranked research queue",
    "filings_monitor": "Check for new filings/events for watched securities",
    "prediction_evaluation": "Evaluate predictions whose horizon has ended",
    "performance_review": "Weekly performance, snapshot and prediction-scorecard review",
    "strategy_validation": "Walk-forward validation of production strategy versions",
    "data_refresh": "Refresh prices, fundamentals, macro and FX for the tracked universe",
    "backtest": "Run a backtest (user-requested)",
    "experiment": "Run a candidate-vs-production strategy experiment",
    "report": "Generate a research report",
    "data_health": "Check provider health and data freshness",
}

DEFAULT_SCHEDULES = [
    # name, job_type, cron (UTC), params
    ("Daily market review", "market_review", "15 22 * * 1-5", {}),
    ("Daily watchlist & portfolio review", "watchlist_review", "30 22 * * 1-5", {}),
    ("Daily research cycle", "research_cycle", "0 23 * * 1-5", {}),
    ("Filings & events monitor", "filings_monitor", "0 */6 * * *", {}),
    ("Prediction evaluation", "prediction_evaluation", "0 6 * * *", {}),
    ("Weekly performance review", "performance_review", "0 8 * * 6", {}),
    ("Weekly strategy validation", "strategy_validation", "0 10 * * 0", {}),
    ("Data health check", "data_health", "*/30 * * * *", {}),
]


def next_run(cron: str, after: datetime) -> datetime:
    return croniter(cron, after).get_next(datetime)


def validate_cron(cron: str) -> None:
    if not croniter.is_valid(cron):
        raise ValueError(f"invalid cron expression '{cron}'")
    base = utcnow()
    first = next_run(cron, base)
    second = next_run(cron, first)
    if (second - first) < timedelta(minutes=5):
        raise ValueError("schedules more frequent than every 5 minutes are not allowed")


def ensure_default_schedules(db: Session, user_id: int) -> None:
    for name, jt, cron, params in DEFAULT_SCHEDULES:
        exists = db.scalar(select(ScheduledJob.id).where(ScheduledJob.user_id == user_id, ScheduledJob.job_type == jt,
                                                         ScheduledJob.name == name))
        if not exists:
            db.add(ScheduledJob(user_id=user_id, name=name, job_type=jt, cron=cron, params=params,
                                next_run_at=next_run(cron, utcnow())))
    db.flush()


def enqueue(db: Session, job_type: str, params: dict | None = None, user_id: int | None = None, trigger: str = "manual",
            scheduled_job_id: int | None = None, max_attempts: int = 3, timeout_seconds: int = 900,
            run_after: datetime | None = None) -> JobRun:
    if job_type not in JOB_TYPES:
        raise ValueError(f"unknown job type '{job_type}'")
    jr = JobRun(user_id=user_id, job_type=job_type, params=params or {}, trigger=trigger, scheduled_job_id=scheduled_job_id,
                max_attempts=max_attempts, timeout_seconds=timeout_seconds, run_after=run_after or utcnow(), status="queued")
    db.add(jr)
    db.flush()
    return jr


def claim_next(db: Session, worker_id: str) -> JobRun | None:
    """Atomically claim the oldest runnable job (commits)."""
    jr = db.scalars(select(JobRun).where(JobRun.status == "queued", JobRun.run_after <= utcnow())
                    .order_by(JobRun.run_after, JobRun.id).limit(1).with_for_update(skip_locked=True)).first()
    if jr is None:
        db.rollback()
        return None
    jr.status = "running"
    jr.attempts += 1
    jr.started_at = utcnow()
    jr.heartbeat_at = utcnow()
    jr.worker_id = worker_id
    db.commit()
    return jr


def mark_failed(db: Session, jr: JobRun, error: str) -> None:
    hist = list(jr.error_history or [])
    hist.append({"attempt": jr.attempts, "at": utcnow().isoformat(), "error": error[:2000]})
    jr.error_history = hist
    jr.error = error[:4000]
    if jr.attempts < jr.max_attempts:
        jr.status = "queued"
        jr.run_after = utcnow() + timedelta(seconds=min(30 * 2 ** (jr.attempts - 1), 3600))
        jr.trigger = "retry" if jr.trigger != "manual" else jr.trigger
    else:
        jr.status = "failed"
        jr.finished_at = utcnow()


def recover_stale(db: Session, stale_after_seconds: int) -> int:
    """Jobs whose worker stopped heart-beating are re-queued (or failed if out of attempts)."""
    cutoff = utcnow() - timedelta(seconds=stale_after_seconds)
    n = 0
    for jr in db.scalars(select(JobRun).where(JobRun.status == "running", JobRun.heartbeat_at < cutoff).with_for_update(skip_locked=True)).all():
        mark_failed(db, jr, f"worker {jr.worker_id} stopped responding (no heartbeat since {jr.heartbeat_at})")
        n += 1
    db.commit()
    return n


def retry(db: Session, jr: JobRun) -> JobRun:
    if jr.status not in ("failed", "cancelled"):
        raise ValueError("only failed or cancelled jobs can be retried")
    new = enqueue(db, jr.job_type, jr.params, jr.user_id, trigger="manual", scheduled_job_id=jr.scheduled_job_id,
                  max_attempts=jr.max_attempts, timeout_seconds=jr.timeout_seconds)
    return new


def cancel(db: Session, jr: JobRun) -> None:
    if jr.status == "queued":
        jr.status = "cancelled"
        jr.finished_at = utcnow()
    elif jr.status == "running":
        jr.status = "cancelled"  # the runner checks this flag between steps
    else:
        raise ValueError(f"cannot cancel a {jr.status} job")


def heartbeat(db: Session, jr_id: int, log: list | None = None) -> bool:
    """Refresh the heartbeat (and step log) from a separate session. Returns False if the job was cancelled."""
    values = {"heartbeat_at": utcnow()}
    if log is not None:
        values["log"] = log
    res = db.execute(update(JobRun).where(JobRun.id == jr_id, JobRun.status == "running").values(**values))
    db.commit()
    return res.rowcount > 0
