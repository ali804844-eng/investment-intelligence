from __future__ import annotations

from datetime import timedelta
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.agent import chat, llm
from app.api.deps import owned
from app.db import get_db, utcnow
from app.models import (
    AgentMemory,
    AgentSettings,
    ApiUsage,
    ChatMessage,
    ChatSession,
    JobRun,
    PendingAction,
    ProviderStatus,
    ScheduledJob,
    User,
    WorkerHeartbeat,
)
from app.providers.registry import get_registry
from app.security import audit, client_ip, get_current_user, rate_limit
from app.services import trading
from app.worker import queue

router = APIRouter(prefix="/api/agent", tags=["agent"])


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: int | None = None


@router.post("/chat", dependencies=[Depends(rate_limit("chat", 60, 3600))])
def post_chat(body: ChatIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        res = chat.handle_message(db, user, body.message, body.session_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    res["pending_actions"] = [pa_out(db.get(PendingAction, i)) for i in res["pending_actions"]]
    return res


@router.get("/sessions")
def sessions(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [{"id": s.id, "title": s.title, "updated_at": s.updated_at.isoformat()}
            for s in db.scalars(select(ChatSession).where(ChatSession.user_id == user.id).order_by(ChatSession.updated_at.desc()).limit(100)).all()]


@router.get("/sessions/{sid}")
def session_messages(sid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = owned(db.get(ChatSession, sid), user.id, "Session")
    msgs = db.scalars(select(ChatMessage).where(ChatMessage.session_id == s.id).order_by(ChatMessage.id)).all()
    return {"id": s.id, "title": s.title, "messages": [{"id": m.id, "role": m.role, "content": m.content, "tool_calls": m.tool_calls,
                                                         "engine": m.engine, "created_at": m.created_at.isoformat()} for m in msgs]}


@router.delete("/sessions/{sid}")
def delete_session(sid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = owned(db.get(ChatSession, sid), user.id, "Session")
    db.delete(s)
    db.commit()
    return {"ok": True}


def pa_out(p: PendingAction) -> dict:
    return {"id": p.id, "action_type": p.action_type, "payload": p.payload, "rationale": p.rationale, "risk_check": p.risk_check,
            "status": p.status, "result": p.result, "created_at": p.created_at.isoformat(),
            "decided_at": p.decided_at.isoformat() if p.decided_at else None}


@router.get("/pending")
def pending(status: str | None = "pending", user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(PendingAction).where(PendingAction.user_id == user.id)
    if status:
        q = q.where(PendingAction.status == status)
    return [pa_out(p) for p in db.scalars(q.order_by(PendingAction.created_at.desc()).limit(200)).all()]


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    confirm: bool = False


@router.post("/pending/{aid}")
def decide(aid: int, body: DecisionIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.decision == "approve" and not body.confirm:
        raise HTTPException(400, "Approval requires explicit confirmation (confirm=true)")
    try:
        pa = trading.decide(db, user.id, aid, body.decision == "approve", ip=client_ip(request))
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    db.commit()
    return pa_out(pa)


@router.get("/memory")
def memory(q: str | None = None, kind: str | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    stmt = select(AgentMemory).where(AgentMemory.user_id == user.id)
    if kind:
        stmt = stmt.where(AgentMemory.kind == kind)
    if q:
        stmt = stmt.where(AgentMemory.content.ilike(f"%{q}%"))
    return [{"id": m.id, "kind": m.kind, "content": m.content, "tags": m.tags, "ref_type": m.ref_type, "ref_id": m.ref_id,
             "created_at": m.created_at.isoformat()} for m in db.scalars(stmt.order_by(AgentMemory.created_at.desc()).limit(300)).all()]


class MemoryIn(BaseModel):
    kind: Literal["research", "decision", "lesson", "preference", "fact"]
    content: str = Field(min_length=1, max_length=4000)


@router.post("/memory")
def add_memory(body: MemoryIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = AgentMemory(user_id=user.id, kind=body.kind, content=body.content, tags=["user"])
    db.add(m)
    db.commit()
    return {"id": m.id}


@router.delete("/memory/{mid}")
def delete_memory(mid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    m = owned(db.get(AgentMemory, mid), user.id, "Memory")
    audit(db, user.id, "memory.delete", "agent_memory", m.id, {"content": m.content[:200]}, ip=client_ip(request))
    db.delete(m)
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ control center
@router.get("/status")
def status(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    st = db.get(AgentSettings, user.id)
    now = utcnow()
    workers = [{"worker_id": w.worker_id, "hostname": w.hostname, "last_seen_at": w.last_seen_at.isoformat(),
                "alive": w.last_seen_at > now - timedelta(minutes=2), "jobs_processed": w.jobs_processed, "started_at": w.started_at.isoformat()}
               for w in db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(10)).all()]
    last_ok = {}
    for jt in queue.JOB_TYPES:
        r = db.scalars(select(JobRun).where(JobRun.user_id == user.id, JobRun.job_type == jt, JobRun.status == "succeeded")
                       .order_by(JobRun.finished_at.desc()).limit(1)).first()
        if r:
            last_ok[jt] = r.finished_at.isoformat() if r.finished_at else None
    counts = dict(db.execute(select(JobRun.status, func.count(JobRun.id)).where(JobRun.user_id == user.id,
                                                                                  JobRun.created_at >= now - timedelta(days=7))
                             .group_by(JobRun.status)).all())
    reg = get_registry()
    prov_rows = {r.provider: r for r in db.scalars(select(ProviderStatus)).all()}
    providers = []
    for p in reg.describe():
        r = prov_rows.get(p["name"])
        providers.append({**p, "status": r.status if r else ("ok" if p["name"] == "sample" and p["configured"] else ("unconfigured" if not p["configured"] else "unknown")),
                          "last_success_at": r.last_success_at.isoformat() if r and r.last_success_at else None,
                          "last_error_at": r.last_error_at.isoformat() if r and r.last_error_at else None,
                          "last_error": r.last_error if r else None, "calls_today": r.calls_today if r and r.calls_date == now.date() else 0})
    usage = [{"provider": u.provider, "day": u.day.isoformat(), "calls": u.calls, "input_tokens": u.input_tokens,
              "output_tokens": u.output_tokens, "est_cost_usd": u.est_cost_usd}
             for u in db.scalars(select(ApiUsage).where(ApiUsage.day >= now.date() - timedelta(days=30),
                                                        (ApiUsage.user_id == user.id) | (ApiUsage.user_id.is_(None)))
                                 .order_by(ApiUsage.day.desc())).all()]
    alive = any(w["alive"] for w in workers)
    return {
        "settings": settings_out(st) if st else None,
        "state": st.state if st else "running",
        "continuous_operation": {"verified": alive,
                                 "explanation": "A worker process heart-beat within the last 2 minutes." if alive else
                                 "No worker heartbeat in the last 2 minutes: scheduled research is NOT running. Start the worker process."},
        "workers": workers, "last_success_by_job": last_ok, "job_counts_7d": counts,
        "llm": {"configured": llm.llm_configured(), **llm.budget_status(db, user.id)}, "providers": providers, "usage_30d": usage,
    }


def settings_out(s: AgentSettings) -> dict:
    return {k: getattr(s, k) for k in ("state", "timezone", "operating_start_hour", "operating_end_hour", "operating_weekdays_only",
                                       "daily_llm_budget_usd", "monthly_llm_budget_usd", "daily_provider_call_budget",
                                       "max_reports_per_cycle", "notify_min_severity")}


class AgentSettingsIn(BaseModel):
    state: Literal["running", "paused", "stopped"] | None = None
    timezone: str | None = Field(default=None, max_length=64)
    operating_start_hour: int | None = Field(default=None, ge=0, le=23)
    operating_end_hour: int | None = Field(default=None, ge=1, le=24)
    operating_weekdays_only: bool | None = None
    daily_llm_budget_usd: float | None = Field(default=None, ge=0, le=1000)
    monthly_llm_budget_usd: float | None = Field(default=None, ge=0, le=10000)
    daily_provider_call_budget: int | None = Field(default=None, ge=0, le=100000)
    max_reports_per_cycle: int | None = Field(default=None, ge=0, le=50)
    notify_min_severity: Literal["info", "warning", "critical"] | None = None


@router.put("/settings")
def put_settings(body: AgentSettingsIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    st = db.get(AgentSettings, user.id) or AgentSettings(user_id=user.id)
    db.add(st)
    changes = body.model_dump(exclude_none=True)
    if "timezone" in changes:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(changes["timezone"])
        except (ZoneInfoNotFoundError, ValueError) as e:
            raise HTTPException(400, "unknown timezone") from e
    for k, v in changes.items():
        setattr(st, k, v)
    if st.operating_end_hour <= st.operating_start_hour:
        raise HTTPException(400, "operating_end_hour must be after operating_start_hour")
    if body.state == "stopped":
        for jr in db.scalars(select(JobRun).where(JobRun.user_id == user.id, JobRun.status == "queued",
                                                  JobRun.trigger.in_(["schedule", "retry", "agent", "event"]))).all():
            jr.status = "cancelled"
            jr.finished_at = utcnow()
    st.updated_at = utcnow()
    audit(db, user.id, "agent.settings", "agent_settings", user.id, changes, ip=client_ip(request))
    db.commit()
    return settings_out(st)


def job_out(j: JobRun, full: bool = False) -> dict:
    d = {"id": j.id, "job_type": j.job_type, "status": j.status, "trigger": j.trigger, "attempts": j.attempts,
         "max_attempts": j.max_attempts, "created_at": j.created_at.isoformat(), "run_after": j.run_after.isoformat(),
         "started_at": j.started_at.isoformat() if j.started_at else None,
         "finished_at": j.finished_at.isoformat() if j.finished_at else None, "error": j.error, "worker_id": j.worker_id,
         "scheduled_job_id": j.scheduled_job_id,
         "duration_s": (j.finished_at - j.started_at).total_seconds() if j.finished_at and j.started_at else None}
    if full:
        d |= {"params": j.params, "log": j.log, "result": j.result, "error_history": j.error_history}
    return d


@router.get("/jobs")
def jobs(status: str | None = None, job_type: str | None = None, limit: int = 100, user: User = Depends(get_current_user),
         db: Session = Depends(get_db)):
    q = select(JobRun).where(JobRun.user_id == user.id)
    if status:
        q = q.where(JobRun.status == status)
    if job_type:
        q = q.where(JobRun.job_type == job_type)
    return [job_out(j) for j in db.scalars(q.order_by(JobRun.created_at.desc()).limit(min(limit, 500))).all()]


@router.get("/jobs/{jid}")
def job(jid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return job_out(owned(db.get(JobRun, jid), user.id, "Job"), True)


class EnqueueIn(BaseModel):
    job_type: str
    params: dict = {}


@router.post("/jobs", dependencies=[Depends(rate_limit("jobs", 60, 3600))])
def enqueue_job(body: EnqueueIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.job_type in ("backtest", "experiment", "report"):
        raise HTTPException(400, "Use the dedicated endpoints for this job type")
    try:
        jr = queue.enqueue(db, body.job_type, body.params, user.id, trigger="manual", timeout_seconds=1800)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit(db, user.id, "job.enqueue", "job_run", jr.id, {"job_type": body.job_type}, ip=client_ip(request))
    db.commit()
    return job_out(jr)


@router.post("/jobs/{jid}/retry")
def retry(jid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    j = owned(db.get(JobRun, jid), user.id, "Job")
    try:
        new = queue.retry(db, j)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit(db, user.id, "job.retry", "job_run", j.id, {"new_job": new.id}, ip=client_ip(request))
    db.commit()
    return job_out(new)


@router.post("/jobs/{jid}/cancel")
def cancel(jid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    j = owned(db.get(JobRun, jid), user.id, "Job")
    try:
        queue.cancel(db, j)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    audit(db, user.id, "job.cancel", "job_run", j.id, {}, ip=client_ip(request))
    db.commit()
    return job_out(j)


def sched_out(s: ScheduledJob) -> dict:
    return {"id": s.id, "name": s.name, "job_type": s.job_type, "cron": s.cron, "params": s.params, "enabled": s.enabled,
            "next_run_at": s.next_run_at.isoformat() if s.next_run_at else None,
            "last_run_at": s.last_run_at.isoformat() if s.last_run_at else None, "last_status": s.last_status,
            "description": queue.JOB_TYPES.get(s.job_type)}


@router.get("/schedules")
def schedules(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [sched_out(s) for s in db.scalars(select(ScheduledJob).where(ScheduledJob.user_id == user.id).order_by(ScheduledJob.next_run_at)).all()]


class ScheduleIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    job_type: str
    cron: str = Field(max_length=64)
    params: dict = {}
    enabled: bool = True


@router.post("/schedules")
def create_schedule(body: ScheduleIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.job_type not in queue.JOB_TYPES or body.job_type in ("backtest", "experiment", "report"):
        raise HTTPException(400, "unsupported job type for schedules")
    try:
        queue.validate_cron(body.cron)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    s = ScheduledJob(user_id=user.id, name=body.name, job_type=body.job_type, cron=body.cron, params=body.params,
                     enabled=body.enabled, next_run_at=queue.next_run(body.cron, utcnow()))
    db.add(s)
    db.flush()
    audit(db, user.id, "schedule.create", "scheduled_job", s.id, body.model_dump(), ip=client_ip(request))
    db.commit()
    return sched_out(s)


class SchedulePatch(BaseModel):
    cron: str | None = Field(default=None, max_length=64)
    enabled: bool | None = None


@router.patch("/schedules/{sid}")
def patch_schedule(sid: int, body: SchedulePatch, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = owned(db.get(ScheduledJob, sid), user.id, "Schedule")
    if body.cron is not None:
        try:
            queue.validate_cron(body.cron)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        s.cron = body.cron
        s.next_run_at = queue.next_run(body.cron, utcnow())
    if body.enabled is not None:
        s.enabled = body.enabled
        if body.enabled:
            s.next_run_at = queue.next_run(s.cron, utcnow())
    audit(db, user.id, "schedule.update", "scheduled_job", s.id, body.model_dump(exclude_none=True), ip=client_ip(request))
    db.commit()
    return sched_out(s)


@router.delete("/schedules/{sid}")
def delete_schedule(sid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = owned(db.get(ScheduledJob, sid), user.id, "Schedule")
    audit(db, user.id, "schedule.delete", "scheduled_job", s.id, {"name": s.name}, ip=client_ip(request))
    db.delete(s)
    db.commit()
    return {"ok": True}
