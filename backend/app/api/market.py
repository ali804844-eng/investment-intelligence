from __future__ import annotations

from datetime import date, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import security_by_symbol
from app.core.finance import ratios
from app.core.screening import setups
from app.db import get_db
from app.models import Quote, Security, User
from app.providers.sample import last_business_day
from app.security import audit, client_ip, get_current_user, rate_limit
from app.services import market_data as md
from app.services import research
from app.services.panel import FEATURES, load_panel
from app.services.regime import market_regime

router = APIRouter(prefix="/api", tags=["market"])


@router.get("/market/overview")
def market_overview(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    out: dict[str, list] = {}
    for s in db.scalars(select(Security).where(Security.asset_class.in_(["index", "etf", "bond", "commodity", "fx", "crypto"]))
                        .order_by(Security.asset_class, Security.symbol)).all():
        q = md.get_quote(db, s)
        ds, cl = md.close_series(db, s.id, last_business_day() - timedelta(days=400))
        ytd = None
        if cl:
            base = [c for d, c in zip(ds, cl, strict=True) if d <= date(ds[-1].year - 1, 12, 31)]
            ytd = cl[-1] / base[-1] - 1 if base else None
        out.setdefault(s.asset_class, []).append({
            **md.security_dict(s), "price": q["price"], "change_pct": q.get("change_pct"), "as_of": q.get("as_of"),
            "quality": q["quality"], "freshness": q["freshness"], "source": q.get("source"),
            "ret_1y": (cl[-1] / cl[-252] - 1) if len(cl) > 252 else None, "ret_ytd": ytd,
            "spark": cl[-60:]})
    return {"groups": out, "regime": market_regime(db),
            "notes": ["Quotes are end-of-day unless labelled otherwise. SAMPLE instruments are synthetic.",
                      "Coverage is limited to configured providers; see Settings → Data sources."]}


@router.get("/market/macro")
def macro(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    from app.services.market_data import MACRO_SERIES

    out = []
    for sid in MACRO_SERIES:
        ds, vs, q = md.macro_series(db, sid, last_business_day() - timedelta(days=365 * 3))
        latest = md.macro_latest(db, sid)
        out.append({"series_id": sid, "latest": latest, "quality": q,
                    "history": [[d.isoformat(), v] for d, v in list(zip(ds, vs, strict=True))[:: max(1, len(ds) // 300)]]})
    return {"series": out}


@router.get("/market/regime")
def regime(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return market_regime(db)


@router.get("/market/securities")
def list_securities(user: User = Depends(get_current_user), db: Session = Depends(get_db),
                    asset_class: str | None = None, country: str | None = None, sector: str | None = None,
                    exchange: str | None = None, currency: str | None = None, min_mcap_usd: float | None = None,
                    min_adv_usd: float | None = None, include_sample: bool = True, q: str | None = None,
                    limit: int = Query(200, le=500)):
    stmt = select(Security).where(Security.is_active.is_(True))
    if asset_class:
        stmt = stmt.where(Security.asset_class == asset_class)
    if country:
        stmt = stmt.where(Security.country == country)
    if sector:
        stmt = stmt.where(Security.sector == sector)
    if exchange:
        stmt = stmt.where(Security.exchange == exchange)
    if currency:
        stmt = stmt.where(Security.currency == currency)
    if not include_sample:
        stmt = stmt.where(Security.is_sample.is_(False))
    if q:
        stmt = stmt.where((Security.symbol.ilike(f"%{q}%")) | (Security.name.ilike(f"%{q}%")))
    secs = db.scalars(stmt.order_by(Security.symbol).limit(limit)).all()
    panel = load_panel(db, [s.id for s in secs], start=last_business_day() - timedelta(days=500))
    asof = last_business_day()
    rows = []
    for s in secs:
        f = panel.features(s.id, asof) if s.id in panel.securities else {}
        if min_mcap_usd and (f.get("market_cap_usd") or 0) < min_mcap_usd:
            continue
        if min_adv_usd and (f.get("adv_usd_20d") or 0) < min_adv_usd:
            continue
        qrow = db.get(Quote, s.id)
        rows.append({**md.security_dict(s), "price": qrow.price if qrow else None,
                     "change_pct": (qrow.price / qrow.prev_close - 1) if qrow and qrow.prev_close else None,
                     "quality": qrow.quality if qrow else "unavailable",
                     "as_of": qrow.as_of.isoformat() if qrow else None,
                     **{k: f.get(k) for k in ("market_cap_usd", "pe", "dividend_yield", "mom_12_1", "vol_252d", "adv_usd_20d", "ret_1m")}})
    facets = {
        "asset_class": sorted({s.asset_class for s in db.scalars(select(Security)).all()}),
        "country": sorted({s.country for s in db.scalars(select(Security)).all() if s.country}),
        "sector": sorted({s.sector for s in db.scalars(select(Security)).all() if s.sector}),
        "exchange": sorted({s.exchange for s in db.scalars(select(Security)).all() if s.exchange}),
        "currency": sorted({s.currency for s in db.scalars(select(Security)).all()}),
    }
    return {"rows": rows, "facets": facets, "as_of": asof.isoformat()}


# ------------------------------------------------------------------ securities
@router.get("/securities/search")
def search(q: str = Query(min_length=1, max_length=40), remote: bool = True, user: User = Depends(get_current_user),
           db: Session = Depends(get_db)):
    res = md.search_securities(db, q, include_remote=remote)
    db.commit()
    return {"results": res}


class AddSecurityIn(BaseModel):
    symbol: str = Field(min_length=1, max_length=20, pattern=r"^[A-Za-z0-9.\-]+$")


@router.post("/securities", dependencies=[Depends(rate_limit("add_sec", 30, 3600))])
def add_security(body: AddSecurityIn, request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    try:
        s = md.add_real_security(db, body.symbol.upper())
    except md.DataUnavailable as e:
        db.commit()  # keep provider status updates
        raise HTTPException(424, str(e)) from e
    result = {"security": md.security_dict(s), "prices": None, "fundamentals": None}
    for key, fn in (("prices", md.refresh_prices), ("fundamentals", md.refresh_fundamentals)):
        try:
            result[key] = fn(db, s)
        except md.DataUnavailable as e:
            result[key] = {"status": "unavailable", "error": str(e)}
    audit(db, user.id, "security.add", "security", s.id, {"symbol": s.symbol}, ip=client_ip(request))
    db.commit()
    return result


@router.get("/securities/{symbol}/overview")
def overview(symbol: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return research.security_overview(db, security_by_symbol(db, symbol))


@router.get("/securities/{symbol}/bars")
def bars(symbol: str, days: int = Query(365 * 2, le=365 * 15), user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = security_by_symbol(db, symbol)
    rows = md.get_bars(db, s.id, last_business_day() - timedelta(days=days))
    return {"symbol": s.symbol, "is_sample": s.is_sample, "currency": s.currency,
            "source": rows[-1].source if rows else None, "quality": rows[-1].quality if rows else "unavailable",
            "bars": [{"time": r.date.isoformat(), "open": r.open, "high": r.high, "low": r.low, "close": r.close,
                      "volume": r.volume} for r in rows]}


@router.get("/securities/{symbol}/statements")
def statements(symbol: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = security_by_symbol(db, symbol)
    st = md.get_statements(db, s.id)
    an = ratios.analyze(st["statements"]) if st["statements"] else None
    return {"symbol": s.symbol, "currency": s.currency, "is_sample": s.is_sample, "metrics": ratios.METRICS,
            "statements": st["statements"], "provenance": st["provenance"], "sources": st["sources"],
            "qualities": st["qualities"], "latest_filed": st["latest_filed"], "analysis": an}


class ValuationIn(BaseModel):
    discount_rate: float | None = Field(default=None, gt=0, lt=0.5)
    terminal_growth: float | None = Field(default=None, gt=-0.05, lt=0.1)
    initial_growth: float | None = Field(default=None, gt=-0.5, lt=1.0)
    years: int | None = Field(default=None, ge=3, le=15)
    equity_risk_premium: float | None = Field(default=None, gt=0, lt=0.2)


@router.post("/securities/{symbol}/valuation")
def valuation(symbol: str, body: ValuationIn, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    return research.auto_valuation(db, security_by_symbol(db, symbol), body.model_dump(exclude_none=True))


@router.get("/securities/{symbol}/setups")
def get_setups(symbol: str, costs_bps: float = Query(15.0, ge=0, le=200), user: User = Depends(get_current_user),
               db: Session = Depends(get_db)):
    s = security_by_symbol(db, symbol)
    o = research.ohlcv(db, s.id, 1500)
    evs = research.upcoming_events(db, s.id)
    return {"symbol": s.symbol, "is_sample": s.is_sample, "setups": setups.detect(o, costs_bps, evs),
            "technicals": setups.technical_snapshot(o), "events": evs}


@router.post("/securities/{symbol}/refresh", dependencies=[Depends(rate_limit("refresh", 30, 3600))])
def refresh(symbol: str, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    s = security_by_symbol(db, symbol)
    out = {}
    for key, fn in (("prices", md.refresh_prices), ("fundamentals", md.refresh_fundamentals), ("filings", md.refresh_filings),
                    ("events", md.refresh_events), ("news", md.refresh_news)):
        try:
            out[key] = fn(db, s)
        except Exception as e:  # noqa: BLE001 - report per-source outcome
            out[key] = {"status": "error", "error": str(e)[:300]}
    db.commit()
    return out


@router.get("/features")
def features(user: User = Depends(get_current_user)):
    return FEATURES
