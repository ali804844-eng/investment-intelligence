from __future__ import annotations

from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db, utcnow
from app.models import User
from app.security import (
    COOKIE_NAME,
    audit,
    check_lockout,
    client_ip,
    create_token,
    get_current_user,
    hash_password,
    rate_limit,
    validate_password_strength,
    verify_password,
)
from app.services.bootstrap import bootstrap_user, create_demo_portfolio, ensure_sample_data

router = APIRouter(prefix="/api/auth", tags=["auth"])


class RegisterIn(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10, max_length=200)
    display_name: str = Field(default="", max_length=120)
    base_currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    with_demo: bool = True


class LoginIn(BaseModel):
    email: EmailStr
    password: str = Field(max_length=200)


class ProfileIn(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    base_currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    preferences: dict | None = None


class PasswordIn(BaseModel):
    current_password: str
    new_password: str = Field(min_length=10, max_length=200)


def user_out(u: User) -> dict:
    return {"id": u.id, "email": u.email, "display_name": u.display_name, "base_currency": u.base_currency,
            "preferences": u.preferences or {}, "created_at": u.created_at.isoformat()}


def _set_cookie(resp: Response, token: str) -> None:
    s = get_settings()
    resp.set_cookie(COOKIE_NAME, token, httponly=True, secure=s.cookie_secure, samesite="lax",
                    max_age=s.access_token_minutes * 60, path="/")


@router.post("/register", dependencies=[Depends(rate_limit("register", 5, 3600))])
def register(body: RegisterIn, request: Request, response: Response, db: Session = Depends(get_db)):
    s = get_settings()
    n_users = db.scalar(select(func.count(User.id)))
    if n_users and not s.allow_registration:
        raise HTTPException(403, "Registration is disabled")
    validate_password_strength(body.password)
    if db.scalar(select(User.id).where(func.lower(User.email) == body.email.lower())):
        raise HTTPException(409, "An account with this email already exists")
    u = User(email=body.email.lower(), password_hash=hash_password(body.password), display_name=body.display_name,
             base_currency=body.base_currency)
    db.add(u)
    db.flush()
    bootstrap_user(db, u)
    if body.with_demo:
        ensure_sample_data(db)
        create_demo_portfolio(db, u)
    audit(db, u.id, "auth.register", "user", u.id, ip=client_ip(request))
    db.commit()
    _set_cookie(response, create_token(u.id))
    return user_out(u)


@router.post("/login", dependencies=[Depends(rate_limit("login", 10, 300))])
def login(body: LoginIn, request: Request, response: Response, db: Session = Depends(get_db)):
    u = db.scalar(select(User).where(func.lower(User.email) == body.email.lower()))
    if u is None:
        verify_password(body.password, "$argon2id$v=19$m=65536,t=3,p=4$c2FsdHNhbHQ$aGFzaGhhc2hoYXNoaGFzaA")  # timing equalisation
        raise HTTPException(401, "Invalid email or password")
    check_lockout(u)
    if not verify_password(body.password, u.password_hash) or not u.is_active:
        u.failed_logins = (u.failed_logins or 0) + 1
        if u.failed_logins >= 5:
            u.locked_until = utcnow() + timedelta(minutes=15)
            u.failed_logins = 0
        audit(db, u.id, "auth.login_failed", "user", u.id, ip=client_ip(request))
        db.commit()
        raise HTTPException(401, "Invalid email or password")
    u.failed_logins = 0
    u.locked_until = None
    bootstrap_user(db, u)
    audit(db, u.id, "auth.login", "user", u.id, ip=client_ip(request))
    db.commit()
    _set_cookie(response, create_token(u.id))
    return user_out(u)


@router.post("/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path="/")
    return {"ok": True}


@router.get("/me")
def me(user: User = Depends(get_current_user)):
    return user_out(user)


@router.put("/me")
def update_me(body: ProfileIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.display_name is not None:
        user.display_name = body.display_name
    if body.base_currency is not None:
        user.base_currency = body.base_currency
    if body.preferences is not None:
        user.preferences = {**(user.preferences or {}), **body.preferences}
    db.merge(user)
    audit(db, user.id, "user.update", "user", user.id, body.model_dump(exclude_none=True), ip=client_ip(request))
    db.commit()
    return user_out(user)


@router.post("/change-password", dependencies=[Depends(rate_limit("pw", 5, 900))])
def change_password(body: PasswordIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    u = db.get(User, user.id)
    if not verify_password(body.current_password, u.password_hash):
        raise HTTPException(400, "Current password is incorrect")
    validate_password_strength(body.new_password)
    u.password_hash = hash_password(body.new_password)
    audit(db, u.id, "auth.password_changed", "user", u.id, ip=client_ip(request))
    db.commit()
    return {"ok": True}
