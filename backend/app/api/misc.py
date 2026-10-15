"""Alerts, notifications, settings/system info, audit log, health."""
from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select, text, update
from sqlalchemy.orm import Session

from app.api.deps import owned, owned_portfolio, security_by_symbol
from app.config import get_settings
from app.db import get_db, utcnow
from app.models import Alert, AuditLog, Notification, Security, User, WorkerHeartbeat
from app.providers.registry import get_registry
from app.security import audit, client_ip, get_current_user
from app.services.alerts import ALERT_KINDS, evaluate_alerts, validate_alert
from app.services.notifications import email_configured

router = APIRouter(prefix="/api", tags=["misc"])


class AlertIn(BaseModel):
    kind: str
    symbol: str | None = None
    portfolio_id: int | None = None
    params: dict = {}
    channels: list[str] = ["in_app"]
    cooldown_minutes: int = Field(default=1440, ge=5, le=60 * 24 * 30)


def alert_out(a: Alert, db: Session) -> dict:
    s = db.get(Security, a.security_id) if a.security_id else None
    return {"id": a.id, "kind": a.kind, "symbol": s.symbol if s else None, "is_sample": s.is_sample if s else False,
            "portfolio_id": a.portfolio_id, "params": a.params, "channels": a.channels, "cooldown_minutes": a.cooldown_minutes,
            "is_active": a.is_active, "last_triggered_at": a.last_triggered_at.isoformat() if a.last_triggered_at else None,
            "last_evaluated_at": a.last_evaluated_at.isoformat() if a.last_evaluated_at else None, "created_at": a.created_at.isoformat()}


@router.get("/alerts/kinds")
def alert_kinds(user: User = Depends(get_current_user)):
    return {"kinds": ALERT_KINDS, "channels": ["in_app"] + (["email"] if email_configured() else []),
            "email_configured": email_configured()}


@router.get("/alerts")
def list_alerts(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [alert_out(a, db) for a in db.scalars(select(Alert).where(Alert.user_id == user.id).order_by(Alert.created_at.desc())).all()]


@router.post("/alerts")
def create_alert(body: AlertIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from fastapi import HTTPException

    s = security_by_symbol(db, body.symbol) if body.symbol else None
    if body.portfolio_id:
        owned_portfolio(db, user.id, body.portfolio_id)
    try:
        validate_alert(body.kind, body.params, s.id if s else None, body.portfolio_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    if "email" in body.channels and not email_configured():
        raise HTTPException(400, "Email delivery is not configured (SMTP_HOST/SMTP_FROM)")
    a = Alert(user_id=user.id, kind=body.kind, security_id=s.id if s else None, portfolio_id=body.portfolio_id, params=body.params,
              channels=[c for c in body.channels if c in ("in_app", "email")] or ["in_app"], cooldown_minutes=body.cooldown_minutes)
    db.add(a)
    db.flush()
    audit(db, user.id, "alert.create", "alert", a.id, body.model_dump(), ip=client_ip(request))
    db.commit()
    return alert_out(a, db)


class AlertPatch(BaseModel):
    is_active: bool | None = None
    params: dict | None = None
    cooldown_minutes: int | None = Field(default=None, ge=5, le=60 * 24 * 30)


@router.patch("/alerts/{aid}")
def patch_alert(aid: int, body: AlertPatch, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = owned(db.get(Alert, aid), user.id, "Alert")
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(a, k, v)
    db.commit()
    return alert_out(a, db)


@router.delete("/alerts/{aid}")
def delete_alert(aid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    a = owned(db.get(Alert, aid), user.id, "Alert")
    audit(db, user.id, "alert.delete", "alert", a.id, {"kind": a.kind}, ip=client_ip(request))
    db.delete(a)
    db.commit()
    return {"ok": True}


@router.post("/alerts/evaluate")
def eval_now(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    r = evaluate_alerts(db, user.id)
    db.commit()
    return r


@router.get("/notifications")
def notifications(unread_only: bool = False, limit: int = 100, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    q = select(Notification).where(Notification.user_id == user.id)
    if unread_only:
        q = q.where(Notification.read_at.is_(None))
    rows = db.scalars(q.order_by(Notification.created_at.desc()).limit(min(limit, 500))).all()
    unread = db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read_at.is_(None)))
    return {"unread": unread, "items": [{"id": n.id, "title": n.title, "body": n.body, "severity": n.severity, "category": n.category,
                                         "link": n.link, "read": n.read_at is not None, "channels": n.delivered_channels,
                                         "created_at": n.created_at.isoformat()} for n in rows]}


@router.post("/notifications/{nid}/read")
def mark_read(nid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    n = owned(db.get(Notification, nid), user.id, "Notification")
    n.read_at = n.read_at or utcnow()
    db.commit()
    return {"ok": True}


@router.post("/notifications/read-all")
def read_all(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    db.execute(update(Notification).where(Notification.user_id == user.id, Notification.read_at.is_(None)).values(read_at=utcnow()))
    db.commit()
    return {"ok": True}


@router.get("/settings/providers")
def providers(user: User = Depends(get_current_user)):
    """Provider configuration status. Never returns secret values."""
    return {"providers": get_registry().describe()}


@router.get("/settings/system")
def system(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = get_settings()
    from app.agent.llm import llm_configured

    return {"environment": s.app_env, "llm_configured": llm_configured(), "llm_model": s.anthropic_model if llm_configured() else None,
            "email_configured": email_configured(), "live_trading_enabled": False,
            "live_trading_note": "Live order execution is not implemented. Research and paper trading only.",
            "sample_data_enabled": s.enable_sample_data,
            "production_warnings": s.validate_for_production() if s.is_production else []}


@router.get("/audit")
def audit_log(limit: int = 200, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = db.scalars(select(AuditLog).where(AuditLog.user_id == user.id).order_by(AuditLog.created_at.desc()).limit(min(limit, 1000))).all()
    return [{"id": a.id, "actor": a.actor, "action": a.action, "entity_type": a.entity_type, "entity_id": a.entity_id,
             "details": a.details, "ip": a.ip, "created_at": a.created_at.isoformat()} for a in rows]


@router.get("/health")
def health(db: Session = Depends(get_db)):
    db_ok = True
    try:
        db.execute(text("SELECT 1"))
    except Exception:  # noqa: BLE001
        db_ok = False
    hb = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)).first() if db_ok else None
    return {"status": "ok" if db_ok else "degraded", "database": db_ok,
            "worker_alive": bool(hb and hb.last_seen_at > utcnow() - timedelta(minutes=2)),
            "worker_last_seen": hb.last_seen_at.isoformat() if hb else None}
