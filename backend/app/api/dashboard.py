from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db, utcnow
from app.models import (
    AgentSettings,
    Alert,
    CorporateEvent,
    JobRun,
    Notification,
    PendingAction,
    Portfolio,
    ProviderStatus,
    ResearchRun,
    ScheduledJob,
    Security,
    User,
    Watchlist,
)
from app.providers.registry import get_registry
from app.security import get_current_user
from app.services import market_data as md
from app.services import portfolio as pfs
from app.services.regime import market_regime

router = APIRouter(prefix="/api", tags=["dashboard"])


@router.get("/dashboard")
def dashboard(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    portfolios = []
    combined = {"total_value": 0.0, "cash": 0.0, "unrealized_pnl": 0.0, "realized_pnl": 0.0, "net_contributions": 0.0}
    exposures_src = None
    for pf in db.scalars(select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)).all():
        try:
            st = pfs.portfolio_state(db, pf)
            hist = pfs.portfolio_history(db, pf)
        except Exception as e:  # noqa: BLE001 - one broken portfolio should not hide the dashboard
            portfolios.append({"id": pf.id, "name": pf.name, "error": str(e)[:200]})
            continue
        day = sum((p["market_value"] or 0) * (p["day_change_pct"] or 0) / (1 + (p["day_change_pct"] or 0))
                  for p in st["positions"] if p["market_value"])
        portfolios.append({
            "id": pf.id, "name": pf.name, "kind": pf.kind, "is_hypothetical": pf.is_hypothetical, "base_currency": pf.base_currency,
            "total_value": st["total_value"], "cash": st["cash"], "market_value": st["market_value"],
            "unrealized_pnl": st["unrealized_pnl"], "realized_pnl": st["realized_pnl"], "income": st["income"],
            "net_contributions": st["net_contributions"], "day_pnl": day, "data_quality": st["data_quality"],
            "n_positions": len(st["positions"]), "periods": hist.get("periods", {}), "metrics": hist.get("metrics", {}),
            "benchmark": ({"symbol": hist["benchmark"]["symbol"], "periods": hist["benchmark"]["periods"]} if hist.get("benchmark") else None),
            "spark": {"dates": hist.get("dates", [])[-260:], "twr_index": hist.get("twr_index", [])[-260:],
                      "benchmark": (hist["benchmark"]["values"][-260:] if hist.get("benchmark") else None)},
            "exposures": st["exposures"], "stale_positions": st["stale_positions"], "complete": st["complete"],
        })
        if pf.base_currency == user.base_currency:
            for k in combined:
                combined[k] += st[k] or 0.0
        if exposures_src is None and st["positions"]:
            exposures_src = pf.id
    wl_items = []
    for wl in db.scalars(select(Watchlist).where(Watchlist.user_id == user.id)).all():
        for it in wl.items:
            s = db.get(Security, it.security_id)
            q = md.get_quote(db, s)
            wl_items.append({"watchlist": wl.name, "symbol": s.symbol, "name": s.name, "is_sample": s.is_sample, **{k: q.get(k) for k in
                             ("price", "change_pct", "as_of", "quality", "freshness", "currency")}})
    run = db.scalars(select(ResearchRun).where(ResearchRun.user_id == user.id, ResearchRun.kind == "research_cycle")
                     .order_by(ResearchRun.created_at.desc()).limit(1)).first()
    today = utcnow().date()
    from app.worker.jobs import watched_ids

    ev_ids = list(watched_ids(db, user.id))
    events = []
    if ev_ids:
        for e in db.scalars(select(CorporateEvent).where(CorporateEvent.security_id.in_(ev_ids), CorporateEvent.event_date >= today,
                                                         CorporateEvent.event_date <= today + timedelta(days=45))
                            .order_by(CorporateEvent.event_date).limit(15)).all():
            s = db.get(Security, e.security_id)
            events.append({"symbol": s.symbol, "kind": e.kind, "date": e.event_date.isoformat(), "quality": e.quality})
    st = db.get(AgentSettings, user.id)
    last_ok = db.scalars(select(JobRun).where(JobRun.user_id == user.id, JobRun.status == "succeeded")
                         .order_by(JobRun.finished_at.desc()).limit(1)).first()
    next_job = db.scalars(select(ScheduledJob).where(ScheduledJob.user_id == user.id, ScheduledJob.enabled.is_(True))
                          .order_by(ScheduledJob.next_run_at).limit(1)).first()
    from app.models import WorkerHeartbeat

    hb = db.scalars(select(WorkerHeartbeat).order_by(WorkerHeartbeat.last_seen_at.desc()).limit(1)).first()
    worker_alive = bool(hb and hb.last_seen_at > utcnow() - timedelta(minutes=2))
    reg = get_registry()
    prov_rows = {r.provider: r for r in db.scalars(select(ProviderStatus)).all()}
    providers = [{"name": p["name"], "title": p["title"], "configured": p["configured"],
                  "status": (prov_rows[p["name"]].status if p["name"] in prov_rows else ("ok" if p["name"] == "sample" and p["configured"] else ("unconfigured" if not p["configured"] else "unknown"))),
                  "last_success_at": prov_rows[p["name"]].last_success_at.isoformat() if p["name"] in prov_rows and prov_rows[p["name"]].last_success_at else None}
                 for p in reg.describe()]
    return {
        "user": {"display_name": user.display_name, "base_currency": user.base_currency},
        "combined": combined, "portfolios": portfolios, "regime": market_regime(db), "watchlist": wl_items,
        "research_queue": (run.summary.get("queue", [])[:10] if run else []),
        "research_run": {"id": run.id, "created_at": run.created_at.isoformat()} if run else None,
        "alerts_active": db.scalar(select(func.count(Alert.id)).where(Alert.user_id == user.id, Alert.is_active.is_(True))),
        "notifications": [{"id": n.id, "title": n.title, "severity": n.severity, "category": n.category, "created_at": n.created_at.isoformat(),
                           "read": n.read_at is not None, "link": n.link}
                          for n in db.scalars(select(Notification).where(Notification.user_id == user.id)
                                              .order_by(Notification.created_at.desc()).limit(8)).all()],
        "unread_notifications": db.scalar(select(func.count(Notification.id)).where(Notification.user_id == user.id, Notification.read_at.is_(None))),
        "pending_actions": db.scalar(select(func.count(PendingAction.id)).where(PendingAction.user_id == user.id, PendingAction.status == "pending")),
        "events": events,
        "agent": {"state": st.state if st else "running", "worker_alive": worker_alive,
                  "worker_last_seen": hb.last_seen_at.isoformat() if hb else None,
                  "last_success": {"job_type": last_ok.job_type, "at": last_ok.finished_at.isoformat()} if last_ok and last_ok.finished_at else None,
                  "next_run": {"name": next_job.name, "at": next_job.next_run_at.isoformat()} if next_job and next_job.next_run_at else None},
        "providers": providers,
        "sample_mode": not db.scalar(select(Security.id).where(Security.is_sample.is_(False)).limit(1)),
    }
