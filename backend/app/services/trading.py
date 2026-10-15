"""Paper-trade execution and pending-action approvals.

Only hypothetical portfolios can receive agent-originated trades, and only after explicit user approval. The
risk engine is re-run at approval time (prices may have moved). Live brokerage execution is NOT implemented;
``kind='live'`` portfolios are blocked by the risk engine's live_trading_gate.
"""
from __future__ import annotations

from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.core.risk.engine import pre_trade_check
from app.db import utcnow
from app.models import PendingAction, Portfolio, Security, Transaction, Watchlist, WatchlistItem
from app.providers.sample import last_business_day
from app.security import audit
from app.services import market_data as md
from app.services import portfolio as pfs
from app.services.panel import load_panel

PAPER_COST_BPS = Decimal("10")  # assumed commission+spread for simulated fills


def pre_trade(db: Session, user_id: int, pf: Portfolio, sec: Security, side: str, quantity: float) -> dict:
    q = md.get_quote(db, sec)
    limits = pfs.limits_for(db, user_id, pf.id)
    state = pfs.portfolio_state(db, pf)
    fx = md.fx_rate(db, sec.currency, pf.base_currency)
    panel = load_panel(db, [sec.id])
    adv = panel.features(sec.id, last_business_day()).get("adv_usd_20d") if sec.id in panel.securities else None
    chk = pre_trade_check(
        side=side, quantity=float(quantity), price=q["price"], symbol=sec.symbol, sector=sec.sector, country=sec.country,
        portfolio_kind=pf.kind, portfolio_is_hypothetical=pf.is_hypothetical, security_is_sample=sec.is_sample,
        state=state, limits=limits, price_freshness=md.freshness(q.get("as_of") and _parse(q["as_of"]), limits.stale_price_days),
        adv_usd=adv, fx_to_base=fx["rate"] if fx else None, held_quantity=pfs.held_quantity(state, sec.id),
        live_trading_enabled=get_settings().live_trading_enabled)
    return {"price": q["price"], "fx": fx, "check": chk}


def _parse(s: str):
    from datetime import datetime

    return datetime.fromisoformat(s)


def decide(db: Session, user_id: int, action_id: int, approve: bool, ip: str | None = None) -> PendingAction:
    pa = db.get(PendingAction, action_id)
    if pa is None or pa.user_id != user_id:
        raise ValueError("pending action not found")
    if pa.status != "pending":
        raise ValueError(f"action already {pa.status}")  # duplicate-execution protection
    pa.decided_at = utcnow()
    if not approve:
        pa.status = "rejected"
        audit(db, user_id, "proposal.reject", "pending_action", pa.id, {"type": pa.action_type}, ip=ip)
        return pa
    try:
        if pa.action_type in ("watchlist_add", "watchlist_remove"):
            wl = db.get(Watchlist, pa.payload["watchlist_id"])
            if wl is None or wl.user_id != user_id:
                raise ValueError("watchlist no longer exists")
            item = db.scalar(select(WatchlistItem).where(WatchlistItem.watchlist_id == wl.id,
                                                         WatchlistItem.security_id == pa.payload["security_id"]))
            if pa.action_type == "watchlist_add" and item is None:
                db.add(WatchlistItem(watchlist_id=wl.id, security_id=pa.payload["security_id"], note=pa.rationale[:500], added_by="agent"))
            elif pa.action_type == "watchlist_remove" and item is not None:
                db.delete(item)
            pa.result = {"ok": True}
        elif pa.action_type == "paper_trade":
            pf = db.get(Portfolio, pa.payload["portfolio_id"])
            sec = db.get(Security, pa.payload["security_id"])
            if pf is None or pf.user_id != user_id:
                raise ValueError("portfolio no longer exists")
            if not pf.is_hypothetical or pf.kind == "live":
                raise ValueError("agent trades are limited to hypothetical portfolios")
            chk = pre_trade(db, user_id, pf, sec, pa.payload["side"], pa.payload["quantity"])
            pa.risk_check = chk["check"]
            if not chk["check"]["approved"]:
                pa.status = "failed"
                pa.result = {"ok": False, "reason": chk["check"]["summary"]}
                audit(db, user_id, "proposal.blocked_by_risk", "pending_action", pa.id, {"summary": chk["check"]["summary"]}, ip=ip)
                return pa
            qty = Decimal(str(pa.payload["quantity"]))
            px = Decimal(str(chk["price"]))
            fees = (qty * px * PAPER_COST_BPS / Decimal(10_000)).quantize(Decimal("0.01"))
            t = Transaction(portfolio_id=pf.id, type=pa.payload["side"], security_id=sec.id, trade_date=last_business_day(),
                            quantity=qty, price=px, fees=fees, currency=sec.currency,
                            fx_rate=Decimal(str(chk["fx"]["rate"])) if chk["fx"] else Decimal(1),
                            notes=f"Paper fill from agent proposal #{pa.id} at last close", origin="agent_paper")
            db.add(t)
            db.flush()
            pfs.portfolio_state(db, pf)  # validates ledger (raises on inconsistency)
            pa.result = {"ok": True, "transaction_id": t.id, "fill_price": float(px), "fees": float(fees)}
        else:
            raise ValueError(f"unknown action type {pa.action_type}")
        pa.status = "executed"
        audit(db, user_id, "proposal.approve", "pending_action", pa.id, {"type": pa.action_type, "result": pa.result}, ip=ip)
    except ValueError as e:
        pa.status = "failed"
        pa.result = {"ok": False, "reason": str(e)}
        audit(db, user_id, "proposal.failed", "pending_action", pa.id, {"reason": str(e)}, ip=ip)
    return pa
