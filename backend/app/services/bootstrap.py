"""New-user bootstrap and optional demo data (clearly labelled SAMPLE)."""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import AgentSettings, Portfolio, RiskPolicy, Security, Transaction, User, Watchlist, WatchlistItem
from app.providers.registry import get_registry
from app.services import learning
from app.services import market_data as md
from app.worker.queue import ensure_default_schedules


def bootstrap_user(db: Session, user: User) -> None:
    if db.get(AgentSettings, user.id) is None:
        db.add(AgentSettings(user_id=user.id))
    if not db.scalar(select(RiskPolicy.id).where(RiskPolicy.user_id == user.id, RiskPolicy.portfolio_id.is_(None))):
        db.add(RiskPolicy(user_id=user.id, portfolio_id=None))
    if not db.scalar(select(Watchlist.id).where(Watchlist.user_id == user.id)):
        db.add(Watchlist(user_id=user.id, name="Core watchlist"))
    db.flush()
    learning.ensure_strategy_versions(db, user.id)
    ensure_default_schedules(db, user.id)


def ensure_sample_data(db: Session) -> int:
    """Load the synthetic universe (idempotent). Returns number of instruments."""
    if not get_registry().sample_enabled:
        return 0
    secs = md.ensure_sample_universe(db)
    for s in secs:
        md.refresh_prices(db, s)
        md.refresh_fundamentals(db, s)
        if s.asset_class == "equity":
            md.refresh_filings(db, s)
            md.refresh_events(db, s)
            md.refresh_news(db, s)
    md.refresh_macro(db)
    md.refresh_fx(db)
    return len(secs)


DEMO_HOLDINGS = [("NWND", 60), ("LITW", 40), ("WWIM", 300), ("TRYR", 250), ("BLUY", 200), ("SMBD", 120), ("HUML", 35),
                 ("FABR", 500), ("NLSW", 15)]


def create_demo_portfolio(db: Session, user: User) -> Portfolio | None:
    """A hypothetical paper portfolio of SAMPLE instruments so analytics can be explored before real data exists."""
    if not get_registry().sample_enabled:
        return None
    existing = db.scalar(select(Portfolio).where(Portfolio.user_id == user.id, Portfolio.name == "Demo paper portfolio (SAMPLE)"))
    if existing:
        return existing
    bench = md.find_security(db, "SMBR")
    pf = Portfolio(user_id=user.id, name="Demo paper portfolio (SAMPLE)", kind="paper", base_currency="USD",
                   benchmark_security_id=bench.id if bench else None, is_hypothetical=True,
                   description="Synthetic instruments and prices. For exploring the platform only.")
    db.add(pf)
    db.flush()
    start = date.today() - timedelta(days=3 * 365)
    while start.weekday() >= 5:
        start += timedelta(days=1)
    db.add(Transaction(portfolio_id=pf.id, type="deposit", trade_date=start, amount=Decimal("100000"), currency="USD",
                       notes="Demo funding", origin="manual"))
    for i, (sym, qty) in enumerate(DEMO_HOLDINGS):
        s = md.find_security(db, sym)
        if s is None:
            continue
        d = start + timedelta(days=7 * i + 1)
        while d.weekday() >= 5:
            d += timedelta(days=1)
        px = md.price_on(db, s.id, d)
        if px is None:
            continue
        fx = md.fx_rate(db, s.currency, "USD", d)
        db.add(Transaction(portfolio_id=pf.id, type="buy", security_id=s.id, trade_date=px[0], quantity=Decimal(qty),
                           price=Decimal(str(round(px[1], 4))), fees=Decimal("1.00"), currency=s.currency,
                           fx_rate=Decimal(str(round(fx["rate"], 8))) if fx else Decimal(1), origin="manual",
                           notes="Demo position"))
    trry = md.find_security(db, "TRYR")
    if trry:
        for k in range(1, 9):
            d = start + timedelta(days=91 * k)
            while d.weekday() >= 5:
                d += timedelta(days=1)
            if d < date.today():
                db.add(Transaction(portfolio_id=pf.id, type="dividend", security_id=trry.id, trade_date=d, amount=Decimal("120"),
                                   fees=Decimal("18"), currency="USD", origin="manual", notes="Demo dividend (withholding as fee)"))
    wl = db.scalars(select(Watchlist).where(Watchlist.user_id == user.id).order_by(Watchlist.id)).first()
    if wl:
        for sym in ("NWND", "CNTS", "GRPH", "PRSV", "SMPL500", "KLMT"):
            s = md.find_security(db, sym)
            if s and not db.scalar(select(WatchlistItem.id).where(WatchlistItem.watchlist_id == wl.id, WatchlistItem.security_id == s.id)):
                db.add(WatchlistItem(watchlist_id=wl.id, security_id=s.id, note="Demo (SAMPLE)"))
    db.flush()
    _ = Security
    return pf
