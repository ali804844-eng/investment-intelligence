"""Authentication (argon2 password hashing, signed session tokens in httpOnly cookies), CSRF guard,
rate limiting and audit logging."""
from __future__ import annotations

import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, utcnow
from app.models import AuditLog, User

COOKIE_NAME = "ii_session"
CSRF_HEADER = "x-requested-with"
_ph = PasswordHasher()


def hash_password(pw: str) -> str:
    return _ph.hash(pw)


def verify_password(pw: str, hashed: str) -> bool:
    try:
        return _ph.verify(hashed, pw)
    except (VerifyMismatchError, InvalidHashError):
        return False


def validate_password_strength(pw: str) -> None:
    if len(pw) < 10:
        raise HTTPException(400, "Password must be at least 10 characters")
    if pw.lower() == pw or pw.upper() == pw or not any(c.isdigit() for c in pw):
        raise HTTPException(400, "Password must mix upper/lower case letters and digits")


def create_token(user_id: int) -> str:
    s = get_settings()
    now = datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(minutes=s.access_token_minutes), "typ": "session"}
    return jwt.encode(payload, s.secret_key.get_secret_value(), algorithm="HS256")


def decode_token(token: str) -> int | None:
    try:
        payload = jwt.decode(token, get_settings().secret_key.get_secret_value(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    if payload.get("typ") != "session":
        return None
    try:
        return int(payload["sub"])
    except (KeyError, ValueError):
        return None


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    token = request.cookies.get(COOKIE_NAME)
    auth = request.headers.get("authorization", "")
    if not token and auth.lower().startswith("bearer "):
        token = auth[7:]
    uid = decode_token(token) if token else None
    if uid is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    user = db.get(User, uid)
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Not authenticated")
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.cookies.get(COOKIE_NAME):
        # Cookie-authenticated mutations must carry a custom header, which cross-site forms cannot set
        # without a CORS preflight. Blocks CSRF in combination with SameSite cookies.
        if request.headers.get(CSRF_HEADER) != "ii":
            raise HTTPException(status.HTTP_403_FORBIDDEN, "Missing CSRF header")
    return user


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def audit(db: Session, user_id: int | None, action: str, entity_type: str | None = None, entity_id=None,
          details: dict | None = None, actor: str = "user", ip: str | None = None) -> None:
    db.add(AuditLog(user_id=user_id, actor=actor, action=action, entity_type=entity_type,
                    entity_id=str(entity_id) if entity_id is not None else None, details=_redact(details or {}), ip=ip))


_SENSITIVE = ("password", "secret", "token", "api_key", "apikey", "authorization")


def _redact(d: dict) -> dict:
    out = {}
    for k, v in d.items():
        if any(s in k.lower() for s in _SENSITIVE):
            out[k] = "***"
        elif isinstance(v, dict):
            out[k] = _redact(v)
        else:
            out[k] = v
    return out


class RateLimiter:
    """In-process sliding-window limiter. For multi-instance deployments put a shared limiter (e.g. at the
    reverse proxy or Redis) in front; this still protects a single instance."""

    def __init__(self):
        self._hits: dict[str, deque] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int, window_seconds: float) -> bool:
        now = time.monotonic()
        with self._lock:
            q = self._hits[key]
            while q and q[0] <= now - window_seconds:
                q.popleft()
            if len(q) >= limit:
                return False
            q.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._hits.clear()


limiter = RateLimiter()


def rate_limit(name: str, limit: int, window: float):
    def dep(request: Request):
        if get_settings().app_env == "test" and request.headers.get("x-test-ratelimit") != "on":
            return
        if not limiter.check(f"{name}:{client_ip(request)}", limit, window):
            raise HTTPException(status.HTTP_429_TOO_MANY_REQUESTS, "Too many requests, slow down")

    return dep


def check_lockout(user: User) -> None:
    if user.locked_until and user.locked_until > utcnow():
        raise HTTPException(status.HTTP_423_LOCKED, "Account temporarily locked after repeated failed logins")
