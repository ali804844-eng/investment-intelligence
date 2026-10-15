"""In-app notifications with de-duplication, plus optional email delivery when SMTP is configured."""
from __future__ import annotations

import logging
import smtplib
from datetime import timedelta
from email.message import EmailMessage

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import utcnow
from app.models import AgentSettings, Notification, User

log = logging.getLogger(__name__)
SEVERITY_ORDER = {"info": 0, "warning": 1, "critical": 2}


def notify(db: Session, user_id: int, title: str, body: str = "", severity: str = "info", category: str = "general",
           link: str | None = None, dedupe_key: str | None = None, dedupe_hours: float = 24.0,
           channels: list[str] | None = None) -> Notification | None:
    """Create a notification unless an identical ``dedupe_key`` fired within ``dedupe_hours`` or the severity is
    below the user's configured minimum."""
    settings = db.get(AgentSettings, user_id)
    if settings and SEVERITY_ORDER.get(severity, 0) < SEVERITY_ORDER.get(settings.notify_min_severity, 0):
        return None
    if dedupe_key:
        since = utcnow() - timedelta(hours=dedupe_hours)
        if db.scalar(select(Notification.id).where(Notification.user_id == user_id, Notification.dedupe_key == dedupe_key,
                                                   Notification.created_at >= since).limit(1)):
            return None
    n = Notification(user_id=user_id, title=title[:300], body=body, severity=severity, category=category, link=link,
                     dedupe_key=dedupe_key, delivered_channels=["in_app"])
    db.add(n)
    db.flush()
    if channels and "email" in channels:
        user = db.get(User, user_id)
        if user and send_email(user.email, f"[Investment Intelligence] {title}", body):
            n.delivered_channels = ["in_app", "email"]
    return n


def email_configured() -> bool:
    s = get_settings()
    return bool(s.smtp_host and s.smtp_from)


def send_email(to: str, subject: str, body: str) -> bool:
    s = get_settings()
    if not email_configured():
        return False
    msg = EmailMessage()
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg["Subject"] = subject[:200]
    msg.set_content(body + "\n\n— Research notification. Not investment advice.")
    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=20) as smtp:
            smtp.starttls()
            if s.smtp_user and s.smtp_password:
                smtp.login(s.smtp_user, s.smtp_password.get_secret_value())
            smtp.send_message(msg)
        return True
    except Exception as e:  # noqa: BLE001 - delivery failures must not break jobs
        log.warning("email delivery failed: %s", type(e).__name__)
        return False
