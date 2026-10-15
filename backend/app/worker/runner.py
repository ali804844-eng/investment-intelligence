"""Worker process: scheduler tick + queue consumer.

Run with:  python -m app.worker.runner
Several workers may run concurrently; claiming uses SKIP LOCKED. The scheduler tick is guarded by a Postgres
advisory lock so only one worker enqueues scheduled jobs at a time.
"""
from __future__ import annotations

import logging
import os
import signal
import socket
import time
import traceback
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, text

from app.config import get_settings
from app.db import SessionLocal, utcnow
from app.models import AgentSettings, JobRun, ScheduledJob, WorkerHeartbeat
from app.services.notifications import notify
from app.worker import queue
from app.worker.jobs import HANDLERS, JobCancelled, JobContext

log = logging.getLogger("worker")
SCHED_LOCK_ID = 87_421_001
VERSION = "1.0"
_stop = False


def _handle_signal(signum, frame):  # noqa: ARG001
    global _stop
    _stop = True
    log.info("signal %s received; finishing current job then exiting", signum)


def within_hours(s: AgentSettings | None, now: datetime) -> bool:
    if s is None:
        return True
    try:
        local = now.astimezone(ZoneInfo(s.timezone))
    except Exception:  # noqa: BLE001
        local = now
    if s.operating_weekdays_only and local.weekday() >= 5:
        return False
    return s.operating_start_hour <= local.hour < s.operating_end_hour


def scheduler_tick() -> int:
    """Enqueue due scheduled jobs. Returns number enqueued."""
    db = SessionLocal()
    n = 0
    try:
        got = db.execute(text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": SCHED_LOCK_ID}).scalar()
        if not got:
            db.rollback()
            return 0
        now = utcnow()
        for sj in db.scalars(select(ScheduledJob).where(ScheduledJob.enabled.is_(True), ScheduledJob.next_run_at <= now)).all():
            st = db.get(AgentSettings, sj.user_id)
            state = st.state if st else "running"
            sj.next_run_at = queue.next_run(sj.cron, now)
            if state != "running":
                sj.last_status = f"skipped (agent {state})"
                continue
            if not within_hours(st, now):
                sj.last_status = "skipped (outside operating hours)"
                continue
            running = db.scalar(select(JobRun.id).where(JobRun.scheduled_job_id == sj.id, JobRun.status.in_(["queued", "running"])).limit(1))
            if running:
                sj.last_status = "skipped (previous run still active)"
                continue
            queue.enqueue(db, sj.job_type, sj.params, sj.user_id, trigger="schedule", scheduled_job_id=sj.id,
                          max_attempts=sj.max_attempts, timeout_seconds=sj.timeout_seconds)
            sj.last_run_at = now
            sj.last_status = "enqueued"
            n += 1
        db.commit()
    finally:
        db.close()
    return n


def _agent_state(db, user_id: int | None) -> str:
    if user_id is None:
        return "running"
    s = db.get(AgentSettings, user_id)
    return s.state if s else "running"


def run_one(worker_id: str) -> bool:
    """Claim and execute one job. Returns True if a job was processed."""
    db = SessionLocal()
    try:
        jr = queue.claim_next(db, worker_id)
        if jr is None:
            return False
        state = _agent_state(db, jr.user_id)
        if state != "running" and jr.trigger in ("schedule", "retry", "event", "agent"):
            jr.status = "queued"
            jr.attempts -= 1
            jr.run_after = utcnow() + timedelta(minutes=15)
            jr.worker_id = None
            if state == "stopped":
                jr.status = "cancelled"
                jr.finished_at = utcnow()
            db.commit()
            return True
        handler = HANDLERS.get(jr.job_type)
        hb_db = SessionLocal()
        jr_id = jr.id
        ctx = JobContext(db, jr, heartbeat=lambda log=None: queue.heartbeat(hb_db, jr_id, log))
        try:
            if handler is None:
                raise RuntimeError(f"no handler for job type {jr.job_type}")
            result = handler(ctx)
            db.commit()
            db.refresh(jr)
            if jr.status == "cancelled":
                jr.finished_at = utcnow()
            else:
                jr.status = "succeeded"
                jr.result = _safe(result)
                jr.finished_at = utcnow()
                jr.error = None
            jr.log = ctx.steps
            if jr.scheduled_job_id:
                sj = db.get(ScheduledJob, jr.scheduled_job_id)
                if sj:
                    sj.last_status = "succeeded"
            db.commit()
        except JobCancelled:
            db.rollback()
            db.refresh(jr)
            jr.status = "cancelled"
            jr.finished_at = utcnow()
            jr.log = ctx.steps
            db.commit()
        except Exception as e:  # noqa: BLE001 - every failure is recorded and retried
            db.rollback()
            db.refresh(jr)
            err = f"{type(e).__name__}: {e}"
            log.warning("job %s (%s) failed: %s", jr.id, jr.job_type, err)
            jr.log = ctx.steps + [{"t": utcnow().isoformat(), "step": "error", "level": "error", "message": err[:1000],
                                   "data": traceback.format_exc()[-3000:]}]
            queue.mark_failed(db, jr, err)
            if jr.status == "failed" and jr.user_id:
                notify(db, jr.user_id, f"Background job failed: {jr.job_type}", f"{err[:500]}\nAttempts: {jr.attempts}. "
                       "Inspect and retry it from the Agent Control Center.", "warning", "agent", "/agent",
                       dedupe_key=f"jobfail:{jr.job_type}:{err[:80]}", dedupe_hours=6)
            if jr.scheduled_job_id:
                sj = db.get(ScheduledJob, jr.scheduled_job_id)
                if sj:
                    sj.last_status = "failed" if jr.status == "failed" else "retrying"
            db.commit()
        finally:
            hb_db.close()
        hb = db.get(WorkerHeartbeat, worker_id)
        if hb:
            hb.jobs_processed += 1
            db.commit()
        return True
    finally:
        db.close()


def _safe(x):
    from app.services.research import _jsonable

    return _jsonable(x)


def beat(worker_id: str) -> None:
    db = SessionLocal()
    try:
        hb = db.get(WorkerHeartbeat, worker_id)
        if hb is None:
            hb = WorkerHeartbeat(worker_id=worker_id, hostname=socket.gethostname(), version=VERSION, jobs_processed=0)
            db.add(hb)
        hb.last_seen_at = utcnow()
        db.commit()
    finally:
        db.close()


def main() -> None:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"), format="%(asctime)s %(levelname)s %(name)s %(message)s")
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    s = get_settings()
    worker_id = f"{socket.gethostname()}:{os.getpid()}"
    log.info("worker %s starting (poll %.1fs)", worker_id, s.worker_poll_seconds)
    last_sched = 0.0
    last_recover = 0.0
    while not _stop:
        now = time.monotonic()
        try:
            beat(worker_id)
            if now - last_sched >= 20:
                n = scheduler_tick()
                if n:
                    log.info("scheduler enqueued %d job(s)", n)
                last_sched = now
            if now - last_recover >= 60:
                db = SessionLocal()
                try:
                    r = queue.recover_stale(db, s.worker_stale_after_seconds)
                    if r:
                        log.warning("recovered %d stale job(s)", r)
                finally:
                    db.close()
                last_recover = now
            if not run_one(worker_id):
                time.sleep(s.worker_poll_seconds)
        except Exception:  # noqa: BLE001 - keep the worker alive; DB outages are retried
            log.exception("worker loop error")
            time.sleep(min(30, s.worker_poll_seconds * 4))
    log.info("worker %s stopped", worker_id)


if __name__ == "__main__":
    main()
