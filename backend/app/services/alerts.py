"""Alert evaluation with cooldowns. Each alert kind has a small, explicit rule."""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Alert, CorporateEvent, Filing, NewsItem, PendingAction, Portfolio, ProviderStatus, ResearchReport, Security
from app.services import market_data as md
from app.services.notifications import notify
from app.services.panel import load_panel
from app.providers.sample import last_business_day

ALERT_KINDS = {
    "price_above": {"params": {"threshold": "number"}, "needs": "security"},
    "price_below": {"params": {"threshold": "number"}, "needs": "security"},
    "pct_move": {"params": {"pct": "number (e.g. 0.05 for 5%)"}, "needs": "security"},
    "valuation": {"params": {"feature": "pe|ev_ebitda|fcf_yield|dividend_yield|ps", "op": "<=|>=", "value": "number"}, "needs": "security"},
    "filing": {"params": {"forms": "list, e.g. ['10-K','10-Q','8-K']"}, "needs": "security"},
    "earnings": {"params": {"days_before": "int"}, "needs": "security"},
    "dividend": {"params": {"days_before": "int"}, "needs": "security"},
    "news": {"params": {"min_abs_sentiment": "number 0..1"}, "needs": "security"},
    "thesis": {"params": {}, "needs": "security"},
    "concentration": {"params": {}, "needs": "portfolio"},
    "drawdown": {"params": {"max_drawdown": "number (e.g. 0.1)"}, "needs": "portfolio"},
    "data_feed": {"params": {}, "needs": None},
    "research_task": {"params": {}, "needs": None},
}


def validate_alert(kind: str, params: dict, security_id: int | None, portfolio_id: int | None) -> None:
    spec = ALERT_KINDS.get(kind)
    if spec is None:
        raise ValueError(f"unknown alert kind '{kind}'")
    if spec["needs"] == "security" and not security_id:
        raise ValueError(f"'{kind}' alerts require a security")
    if spec["needs"] == "portfolio" and not portfolio_id:
        raise ValueError(f"'{kind}' alerts require a portfolio")
    for k in spec["params"]:
        if k not in params and kind not in ("filing", "earnings", "dividend", "news"):
            raise ValueError(f"missing parameter '{k}'")
    if kind == "valuation" and params.get("op") not in ("<=", ">="):
        raise ValueError("valuation op must be <= or >=")


def _check(db: Session, a: Alert) -> tuple[bool, str, str, str]:
    """Returns (triggered, title, body, severity)."""
    sec = db.get(Security, a.security_id) if a.security_id else None
    p = a.params or {}
    since = a.last_evaluated_at or (utcnow() - timedelta(days=1))
    tag = " [SAMPLE]" if sec is not None and sec.is_sample else ""
    if a.kind in ("price_above", "price_below", "pct_move"):
        q = md.get_quote(db, sec)
        if q["price"] is None:
            return False, "", "", "info"
        if q["freshness"]["status"] != "fresh":
            return False, "", "", "info"  # never alert on stale prices
        if a.kind == "price_above" and q["price"] >= float(p["threshold"]):
            return True, f"{sec.symbol} at {q['price']:.2f} ≥ {p['threshold']}{tag}", f"Price as of {q['as_of']} ({q['quality']}).", "warning"
        if a.kind == "price_below" and q["price"] <= float(p["threshold"]):
            return True, f"{sec.symbol} at {q['price']:.2f} ≤ {p['threshold']}{tag}", f"Price as of {q['as_of']} ({q['quality']}).", "warning"
        if a.kind == "pct_move" and q.get("change_pct") is not None and abs(q["change_pct"]) >= float(p["pct"]):
            return True, f"{sec.symbol} moved {q['change_pct']:+.1%}{tag}", f"Close-to-close move as of {q['as_of']}.", "warning"
    elif a.kind == "valuation":
        panel = load_panel(db, [sec.id])
        f = panel.features(sec.id, last_business_day())
        v = f.get(p["feature"])
        if v is not None and ((p["op"] == "<=" and v <= float(p["value"])) or (p["op"] == ">=" and v >= float(p["value"]))):
            return True, f"{sec.symbol} {p['feature']} = {v:.3g} ({p['op']} {p['value']}){tag}", "Valuation threshold crossed.", "info"
    elif a.kind == "filing":
        forms = p.get("forms")
        q = select(Filing).where(Filing.security_id == sec.id, Filing.fetched_at > since)
        rows = [f for f in db.scalars(q).all() if not forms or f.form in forms]
        if rows:
            return True, f"New filing(s) for {sec.symbol}: {', '.join(sorted({r.form for r in rows}))}{tag}", \
                "\n".join(f"{r.form} filed {r.filed_date} {r.url or ''}" for r in rows[:5]), "info"
    elif a.kind in ("earnings", "dividend"):
        days = int(p.get("days_before", 7))
        today = utcnow().date()
        ev = db.scalars(select(CorporateEvent).where(CorporateEvent.security_id == sec.id, CorporateEvent.kind == a.kind,
                                                     CorporateEvent.event_date >= today,
                                                     CorporateEvent.event_date <= today + timedelta(days=days))).first()
        if ev:
            return True, f"{sec.symbol} {a.kind} on {ev.event_date}{tag}", f"Source: {ev.source} ({ev.quality}).", "info"
    elif a.kind == "news":
        thr = float(p.get("min_abs_sentiment", 0.0))
        rows = [n for n in db.scalars(select(NewsItem).where(NewsItem.security_id == sec.id, NewsItem.fetched_at > since)).all()
                if n.sentiment is None or abs(n.sentiment) >= thr]
        if rows:
            return True, f"{len(rows)} new news item(s) for {sec.symbol}{tag}", \
                "\n".join(f"- {n.title[:140]} ({n.publisher})" for n in rows[:5]) + "\n(Third-party content, unverified.)", "info"
    elif a.kind == "thesis":
        reps = db.scalars(select(ResearchReport).where(ResearchReport.user_id == a.user_id, ResearchReport.security_id == sec.id)
                          .order_by(ResearchReport.created_at.desc()).limit(2)).all()
        if len(reps) == 2 and reps[0].conclusion != reps[1].conclusion and reps[0].created_at > since:
            return True, f"Thesis change on {sec.symbol}: {reps[1].conclusion} → {reps[0].conclusion}{tag}", \
                reps[0].sections.get("executive_summary", ""), "warning"
    elif a.kind in ("concentration", "drawdown"):
        from app.services.portfolio import portfolio_history, risk_report

        pf = db.get(Portfolio, a.portfolio_id)
        if pf is None:
            return False, "", "", "info"
        if a.kind == "concentration":
            rr = risk_report(db, pf, a.user_id)
            if rr["breaches"]:
                return True, f"Risk limit breach in '{pf.name}'", "\n".join(c["message"] for c in rr["breaches"][:6]), "critical"
        else:
            h = portfolio_history(db, pf)
            dd = h["metrics"].get("current_drawdown")
            if dd is not None and dd <= -float(p["max_drawdown"]):
                return True, f"'{pf.name}' drawdown {dd:.1%}", f"Threshold −{float(p['max_drawdown']):.0%}.", "critical"
    elif a.kind == "data_feed":
        bad = db.scalars(select(ProviderStatus).where(ProviderStatus.configured.is_(True), ProviderStatus.status == "degraded")).all()
        if bad:
            return True, "Data feed problem: " + ", ".join(b.provider for b in bad), \
                "\n".join(f"{b.provider}: {(b.last_error or '')[:200]}" for b in bad), "warning"
    elif a.kind == "research_task":
        n = len(db.scalars(select(PendingAction.id).where(PendingAction.user_id == a.user_id, PendingAction.status == "pending")).all())
        if n:
            return True, f"{n} agent proposal(s) awaiting your decision", "Review them in the Agent Control Center.", "info"
    return False, "", "", "info"


def evaluate_alerts(db: Session, user_id: int) -> dict:
    fired, errors = [], []
    now = utcnow()
    for a in db.scalars(select(Alert).where(Alert.user_id == user_id, Alert.is_active.is_(True))).all():
        try:
            if a.last_triggered_at and a.last_triggered_at > now - timedelta(minutes=a.cooldown_minutes):
                a.last_evaluated_at = now
                continue
            ok, title, body, sev = _check(db, a)
            a.last_evaluated_at = now
            if ok:
                n = notify(db, user_id, title, body, sev, "alert", link=f"/alerts#{a.id}", dedupe_key=f"alert:{a.id}:{title}",
                           dedupe_hours=a.cooldown_minutes / 60, channels=a.channels)
                a.last_triggered_at = now
                if n:
                    fired.append({"alert_id": a.id, "title": title})
        except Exception as e:  # noqa: BLE001 - one broken alert must not stop the rest
            errors.append({"alert_id": a.id, "error": f"{type(e).__name__}: {e}"})
    db.flush()
    return {"fired": fired, "errors": errors}
