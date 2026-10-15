from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models import Portfolio, Security, Watchlist
from app.services import market_data as md


def owned_portfolio(db: Session, user_id: int, pid: int) -> Portfolio:
    pf = db.get(Portfolio, pid)
    if pf is None or pf.user_id != user_id:
        raise HTTPException(404, "Portfolio not found")
    return pf


def owned_watchlist(db: Session, user_id: int, wid: int) -> Watchlist:
    wl = db.get(Watchlist, wid)
    if wl is None or wl.user_id != user_id:
        raise HTTPException(404, "Watchlist not found")
    return wl


def security_by_symbol(db: Session, symbol: str) -> Security:
    s = md.find_security(db, symbol)
    if s is None:
        raise HTTPException(404, f"Unknown symbol '{symbol}'. Add it via search first.")
    return s


def owned(obj, user_id: int, what: str = "Resource"):
    if obj is None or getattr(obj, "user_id", None) != user_id:
        raise HTTPException(404, f"{what} not found")
    return obj
