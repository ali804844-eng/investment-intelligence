from __future__ import annotations

import csv
import io
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import owned_portfolio, owned_watchlist, security_by_symbol
from app.core.finance.accounting import AccountingError
from app.core.risk.engine import STRESS_SCENARIOS, Limits, position_size, stress_test
from app.db import get_db, utcnow
from app.models import Portfolio, RiskPolicy, Security, Transaction, User, Watchlist, WatchlistItem
from app.security import audit, client_ip, get_current_user
from app.services import market_data as md
from app.services import portfolio as pfs
from app.services.trading import pre_trade

router = APIRouter(prefix="/api", tags=["portfolios"])

KINDS = Literal["long_term", "trading", "research", "paper", "manual", "live"]


class PortfolioIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    kind: KINDS = "manual"
    base_currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    benchmark_symbol: str | None = None
    is_hypothetical: bool = True
    cost_basis_method: Literal["FIFO", "AVG"] = "FIFO"
    description: str | None = Field(default=None, max_length=2000)


class PortfolioPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    benchmark_symbol: str | None = None
    description: str | None = Field(default=None, max_length=2000)
    cost_basis_method: Literal["FIFO", "AVG"] | None = None


def pf_out(pf: Portfolio, db: Session) -> dict:
    b = db.get(Security, pf.benchmark_security_id) if pf.benchmark_security_id else None
    return {"id": pf.id, "name": pf.name, "kind": pf.kind, "base_currency": pf.base_currency, "is_hypothetical": pf.is_hypothetical,
            "cost_basis_method": pf.cost_basis_method, "description": pf.description,
            "benchmark": {"symbol": b.symbol, "is_sample": b.is_sample} if b else None, "created_at": pf.created_at.isoformat()}


@router.get("/portfolios")
def list_portfolios(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [pf_out(p, db) for p in db.scalars(select(Portfolio).where(Portfolio.user_id == user.id).order_by(Portfolio.id)).all()]


@router.post("/portfolios")
def create_portfolio(body: PortfolioIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.kind == "live":
        raise HTTPException(400, "Connected live accounts are not supported yet. Use 'manual' with is_hypothetical=false "
                                 "to track an actual account by entering its transactions.")
    if body.kind == "paper" and not body.is_hypothetical:
        raise HTTPException(400, "Paper portfolios are always hypothetical")
    bench = security_by_symbol(db, body.benchmark_symbol) if body.benchmark_symbol else None
    pf = Portfolio(user_id=user.id, name=body.name, kind=body.kind, base_currency=body.base_currency,
                   benchmark_security_id=bench.id if bench else None, is_hypothetical=body.is_hypothetical,
                   cost_basis_method=body.cost_basis_method, description=body.description)
    db.add(pf)
    db.flush()
    audit(db, user.id, "portfolio.create", "portfolio", pf.id, body.model_dump(), ip=client_ip(request))
    db.commit()
    return pf_out(pf, db)


@router.patch("/portfolios/{pid}")
def patch_portfolio(pid: int, body: PortfolioPatch, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    if body.name is not None:
        pf.name = body.name
    if body.description is not None:
        pf.description = body.description
    if body.benchmark_symbol is not None:
        pf.benchmark_security_id = security_by_symbol(db, body.benchmark_symbol).id if body.benchmark_symbol else None
    if body.cost_basis_method is not None:
        pf.cost_basis_method = body.cost_basis_method
    audit(db, user.id, "portfolio.update", "portfolio", pf.id, body.model_dump(exclude_none=True), ip=client_ip(request))
    db.commit()
    return pf_out(pf, db)


@router.delete("/portfolios/{pid}")
def delete_portfolio(pid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    audit(db, user.id, "portfolio.delete", "portfolio", pf.id, {"name": pf.name}, ip=client_ip(request))
    db.delete(pf)
    db.commit()
    return {"ok": True}


@router.get("/portfolios/{pid}")
def get_portfolio(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    return {"portfolio": pf_out(pf, db), "state": pfs.portfolio_state(db, pf)}


@router.get("/portfolios/{pid}/history")
def history(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return pfs.portfolio_history(db, owned_portfolio(db, user.id, pid))


@router.get("/portfolios/{pid}/risk")
def risk(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return pfs.risk_report(db, owned_portfolio(db, user.id, pid), user.id)


class TxnIn(BaseModel):
    type: Literal["buy", "sell", "dividend", "interest", "fee", "deposit", "withdrawal", "split"]
    trade_date: date
    symbol: str | None = None
    quantity: Decimal | None = Field(default=None, gt=0)
    price: Decimal | None = Field(default=None, ge=0)
    amount: Decimal | None = Field(default=None, ge=0)
    fees: Decimal = Field(default=Decimal(0), ge=0)
    currency: str | None = Field(default=None, pattern="^[A-Z]{3}$")
    fx_rate: Decimal | None = Field(default=None, gt=0)
    split_ratio: Decimal | None = Field(default=None, gt=0)
    notes: str | None = Field(default=None, max_length=1000)

    @field_validator("trade_date")
    @classmethod
    def not_future(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("trade date cannot be in the future")
        return v


def _make_txn(db: Session, pf: Portfolio, body: TxnIn, origin: str = "manual") -> Transaction:
    sec = security_by_symbol(db, body.symbol) if body.symbol else None
    if body.type in ("buy", "sell", "dividend", "split") and sec is None:
        raise HTTPException(400, f"{body.type} requires a symbol")
    if sec is not None:
        has = {p_["is_sample"] for p_ in pfs.portfolio_state(db, pf)["positions"]}
        if sec.is_sample and (not pf.is_hypothetical or False in has):
            raise HTTPException(400, "SAMPLE instruments can only be added to hypothetical portfolios without real holdings")
        if not sec.is_sample and True in has:
            raise HTTPException(400, "This portfolio contains SAMPLE instruments; record real instruments in a separate portfolio")
    ccy = body.currency or (sec.currency if sec else pf.base_currency)
    fx = body.fx_rate
    if fx is None:
        if ccy == pf.base_currency:
            fx = Decimal(1)
        else:
            r = md.fx_rate(db, ccy, pf.base_currency, body.trade_date)
            if r is None:
                raise HTTPException(400, f"No FX rate {ccy}->{pf.base_currency} for {body.trade_date}; provide fx_rate explicitly")
            if r["quality"] == "sample" and not pf.is_hypothetical:
                raise HTTPException(400, "Only SAMPLE FX rates are available; provide fx_rate explicitly for an actual portfolio")
            fx = Decimal(str(round(r["rate"], 10)))
    return Transaction(portfolio_id=pf.id, type=body.type, security_id=sec.id if sec else None, trade_date=body.trade_date,
                       quantity=body.quantity, price=body.price, amount=body.amount, fees=body.fees, currency=ccy, fx_rate=fx,
                       split_ratio=body.split_ratio, notes=body.notes, origin=origin)


def txn_out(t: Transaction, db: Session) -> dict:
    s = db.get(Security, t.security_id) if t.security_id else None
    f = lambda x: float(x) if x is not None else None  # noqa: E731
    return {"id": t.id, "type": t.type, "trade_date": t.trade_date.isoformat(), "symbol": s.symbol if s else None,
            "is_sample": s.is_sample if s else False, "quantity": f(t.quantity), "price": f(t.price), "amount": f(t.amount),
            "fees": f(t.fees), "currency": t.currency, "fx_rate": f(t.fx_rate), "split_ratio": f(t.split_ratio),
            "notes": t.notes, "origin": t.origin, "created_at": t.created_at.isoformat()}


@router.get("/portfolios/{pid}/transactions")
def list_txns(pid: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    return [txn_out(t, db) for t in reversed(pfs.load_txns(db, pf))]


@router.post("/portfolios/{pid}/transactions")
def add_txn(pid: int, body: TxnIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    if pf.kind == "live":
        raise HTTPException(400, "Live portfolios are read-only")
    t = _make_txn(db, pf, body)
    db.add(t)
    db.flush()
    try:
        pfs.portfolio_state(db, pf)  # replay validates the whole ledger with the new entry
    except AccountingError as e:
        db.rollback()
        raise HTTPException(400, str(e)) from e
    audit(db, user.id, "transaction.create", "transaction", t.id, {"portfolio_id": pf.id, **body.model_dump(mode="json")},
          ip=client_ip(request))
    db.commit()
    return txn_out(t, db)


@router.delete("/portfolios/{pid}/transactions/{tid}")
def delete_txn(pid: int, tid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    t = db.get(Transaction, tid)
    if t is None or t.portfolio_id != pf.id or t.deleted_at is not None:
        raise HTTPException(404, "Transaction not found")
    t.deleted_at = utcnow()
    db.flush()
    try:
        pfs.portfolio_state(db, pf)
    except AccountingError as e:
        db.rollback()
        raise HTTPException(400, f"Deleting this transaction would make the ledger inconsistent: {e}") from e
    audit(db, user.id, "transaction.delete", "transaction", t.id, txn_out(t, db), ip=client_ip(request))
    db.commit()
    return {"ok": True}


@router.post("/portfolios/{pid}/import")
async def import_csv(pid: int, file: UploadFile, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """CSV columns: type,trade_date,symbol,quantity,price,amount,fees,currency,fx_rate,notes. All-or-nothing."""
    pf = owned_portfolio(db, user.id, pid)
    raw = await file.read()
    if len(raw) > 2_000_000:
        raise HTTPException(413, "File too large (max 2 MB)")
    rows = list(csv.DictReader(io.StringIO(raw.decode("utf-8-sig"))))
    if len(rows) > 5000:
        raise HTTPException(400, "Too many rows (max 5000)")
    created = []
    for i, r in enumerate(rows, start=2):
        try:
            body = TxnIn(**{k: (v.strip() if isinstance(v, str) and v.strip() != "" else None) for k, v in r.items()
                            if k in TxnIn.model_fields and v not in (None, "")})
            t = _make_txn(db, pf, body, origin="import")
        except (ValueError, InvalidOperation, HTTPException) as e:
            db.rollback()
            detail = e.detail if isinstance(e, HTTPException) else str(e)
            raise HTTPException(400, f"Row {i}: {detail}") from e
        db.add(t)
        created.append(t)
    db.flush()
    try:
        pfs.portfolio_state(db, pf)
    except AccountingError as e:
        db.rollback()
        raise HTTPException(400, f"Import rejected, ledger inconsistent: {e}") from e
    audit(db, user.id, "transaction.import", "portfolio", pf.id, {"rows": len(created), "filename": file.filename}, ip=client_ip(request))
    db.commit()
    return {"imported": len(created)}


class RebalanceIn(BaseModel):
    targets: dict[str, float]
    cost_bps: float = Field(default=15.0, ge=0, le=500)


@router.post("/portfolios/{pid}/rebalance-simulation")
def rebalance(pid: int, body: RebalanceIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    st = pfs.portfolio_state(db, pf)
    if any(w < 0 or w > 1 for w in body.targets.values()):
        raise HTTPException(400, "weights must be between 0 and 1")
    try:
        sim = pfs.rebalance_simulation(st, {k.upper(): v for k, v in body.targets.items()}, body.cost_bps)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    limits = pfs.limits_for(db, user.id, pf.id)
    sim["limit_violations"] = [f"{k} target {v:.1%} exceeds position limit {limits.max_position_pct:.0%}"
                               for k, v in body.targets.items() if v > limits.max_position_pct]
    return sim


class StressIn(BaseModel):
    scenario: str = "equity_crash"
    custom: dict | None = None


@router.get("/stress-scenarios")
def scenarios(user: User = Depends(get_current_user)):
    return {k: v["title"] for k, v in STRESS_SCENARIOS.items()}


@router.post("/portfolios/{pid}/stress")
def stress(pid: int, body: StressIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    if body.scenario not in STRESS_SCENARIOS and not body.custom:
        raise HTTPException(400, "unknown scenario")
    st = pfs.portfolio_state(db, pf)
    return stress_test(st["positions"], pf.base_currency, body.scenario, body.custom)


class PreTradeIn(BaseModel):
    symbol: str
    side: Literal["buy", "sell"]
    quantity: float = Field(gt=0)


@router.post("/portfolios/{pid}/pretrade-check")
def pretrade(pid: int, body: PreTradeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    s = security_by_symbol(db, body.symbol)
    r = pre_trade(db, user.id, pf, s, body.side, body.quantity)
    return {"price": r["price"], "fx": r["fx"], **r["check"]}


class SizeIn(BaseModel):
    entry: float = Field(gt=0)
    stop: float = Field(gt=0)
    risk_per_trade_pct: float = Field(default=0.01, gt=0, le=0.1)


@router.post("/portfolios/{pid}/position-size")
def size(pid: int, body: SizeIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    pf = owned_portfolio(db, user.id, pid)
    st = pfs.portfolio_state(db, pf)
    lim = pfs.limits_for(db, user.id, pf.id)
    try:
        return position_size(st["total_value"], body.entry, body.stop, body.risk_per_trade_pct, lim.max_position_pct)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ------------------------------------------------------------------ risk policy
class PolicyIn(BaseModel):
    portfolio_id: int | None = None
    max_position_pct: float = Field(gt=0, le=1)
    max_sector_pct: float = Field(gt=0, le=1)
    max_country_pct: float = Field(gt=0, le=1)
    max_drawdown_pct: float = Field(gt=0, le=1)
    max_annual_vol: float = Field(gt=0, le=2)
    min_avg_dollar_volume: float = Field(ge=0)
    max_pct_of_adv: float = Field(gt=0, le=1)
    stale_price_days: int = Field(ge=0, le=60)
    stale_fundamentals_days: int = Field(ge=30, le=1000)
    max_pairwise_corr: float = Field(gt=0, le=1)


@router.get("/risk/policy")
def get_policy(portfolio_id: int | None = None, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if portfolio_id:
        owned_portfolio(db, user.id, portfolio_id)
    lim = pfs.limits_for(db, user.id, portfolio_id)
    return {"portfolio_id": portfolio_id, **lim.__dict__,
            "note": "Limits are enforced by the deterministic risk engine. The AI agent cannot modify them."}


@router.put("/risk/policy")
def put_policy(body: PolicyIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.portfolio_id:
        owned_portfolio(db, user.id, body.portfolio_id)
    pol = db.scalar(select(RiskPolicy).where(RiskPolicy.user_id == user.id,
                                             RiskPolicy.portfolio_id.is_(None) if body.portfolio_id is None else RiskPolicy.portfolio_id == body.portfolio_id))
    before = {k: getattr(pol, k) for k in Limits.__dataclass_fields__} if pol else None
    if pol is None:
        pol = RiskPolicy(user_id=user.id, portfolio_id=body.portfolio_id)
        db.add(pol)
    for k, v in body.model_dump(exclude={"portfolio_id"}).items():
        setattr(pol, k, v)
    pol.updated_at = utcnow()
    audit(db, user.id, "risk_policy.update", "risk_policy", body.portfolio_id or "global",
          {"before": before, "after": body.model_dump()}, ip=client_ip(request))
    db.commit()
    return {"ok": True}


# ------------------------------------------------------------------ watchlists
class WatchlistIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class WatchItemIn(BaseModel):
    symbol: str
    note: str | None = Field(default=None, max_length=1000)


@router.get("/watchlists")
def list_watchlists(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    out = []
    for wl in db.scalars(select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.id)).all():
        items = []
        for it in wl.items:
            s = db.get(Security, it.security_id)
            q = md.get_quote(db, s)
            items.append({"id": it.id, "symbol": s.symbol, "name": s.name, "note": it.note, "added_by": it.added_by,
                          "is_sample": s.is_sample, "asset_class": s.asset_class, "sector": s.sector,
                          **{k: q.get(k) for k in ("price", "change_pct", "as_of", "quality", "freshness", "currency")}})
        out.append({"id": wl.id, "name": wl.name, "items": items})
    return out


@router.post("/watchlists")
def create_watchlist(body: WatchlistIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wl = Watchlist(user_id=user.id, name=body.name)
    db.add(wl)
    db.flush()
    audit(db, user.id, "watchlist.create", "watchlist", wl.id, {"name": body.name}, ip=client_ip(request))
    db.commit()
    return {"id": wl.id, "name": wl.name, "items": []}


@router.delete("/watchlists/{wid}")
def delete_watchlist(wid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wl = owned_watchlist(db, user.id, wid)
    audit(db, user.id, "watchlist.delete", "watchlist", wl.id, {"name": wl.name}, ip=client_ip(request))
    db.delete(wl)
    db.commit()
    return {"ok": True}


@router.post("/watchlists/{wid}/items")
def add_item(wid: int, body: WatchItemIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wl = owned_watchlist(db, user.id, wid)
    s = security_by_symbol(db, body.symbol)
    if db.scalar(select(WatchlistItem.id).where(WatchlistItem.watchlist_id == wl.id, WatchlistItem.security_id == s.id)):
        raise HTTPException(409, "Already on this watchlist")
    it = WatchlistItem(watchlist_id=wl.id, security_id=s.id, note=body.note)
    db.add(it)
    db.flush()
    audit(db, user.id, "watchlist.add", "watchlist", wl.id, {"symbol": s.symbol}, ip=client_ip(request))
    db.commit()
    return {"id": it.id}


@router.delete("/watchlists/{wid}/items/{iid}")
def remove_item(wid: int, iid: int, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    wl = owned_watchlist(db, user.id, wid)
    it = db.get(WatchlistItem, iid)
    if it is None or it.watchlist_id != wl.id:
        raise HTTPException(404, "Item not found")
    audit(db, user.id, "watchlist.remove", "watchlist", wl.id, {"security_id": it.security_id}, ip=client_ip(request))
    db.delete(it)
    db.commit()
    return {"ok": True}
