"""Market data service: fetches through provider adapters, persists with attribution, and answers read queries
with explicit freshness/quality metadata."""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import (
    ApiUsage,
    CorporateEvent,
    Filing,
    FundamentalFact,
    FxRate,
    MacroObservation,
    NewsItem,
    PriceBar,
    ProviderStatus,
    Quote,
    Security,
)
from app.providers.base import NotSupported, Provider, ProviderError, SecurityInfo
from app.providers.registry import Registry, get_registry
from app.providers.sample import last_business_day

log = logging.getLogger(__name__)


class DataUnavailable(Exception):
    pass


# ----------------------------------------------------------------- provider bookkeeping
def record_call(db: Session, provider: Provider, ok: bool, error: str | None = None, calls: int = 1) -> None:
    today = utcnow().date()
    st = db.get(ProviderStatus, provider.name)
    if st is None:
        st = ProviderStatus(provider=provider.name, calls_today=0)
        db.add(st)
    st.configured = provider.is_configured()
    if st.calls_date != today:
        st.calls_today = 0
        st.calls_date = today
    st.calls_today = (st.calls_today or 0) + calls
    now = utcnow()
    if ok:
        st.status = "ok"
        st.last_success_at = now
    else:
        st.status = "unconfigured" if isinstance(error, str) and "not configured" in error else "degraded"
        st.last_error_at = now
        st.last_error = (error or "")[:1000]
    st.updated_at = now
    stmt = pg_insert(ApiUsage).values(provider=provider.name, user_id=None, day=today, calls=calls)
    stmt = stmt.on_conflict_do_update(constraint="uq_usage", set_={"calls": ApiUsage.calls + calls})
    db.execute(stmt)


def provider_budget_ok(db: Session, provider: Provider) -> bool:
    if provider.daily_limit is None:
        return True
    st = db.get(ProviderStatus, provider.name)
    if st is None or st.calls_date != utcnow().date():
        return True
    return st.calls_today < provider.daily_limit


def _call(db: Session, provider: Provider, fn, *args, **kwargs):
    if not provider_budget_ok(db, provider):
        raise ProviderError(f"{provider.name}: daily call budget ({provider.daily_limit}) reached")
    before = provider.calls
    try:
        res = fn(*args, **kwargs)
    except NotSupported:
        raise
    except ProviderError as e:
        record_call(db, provider, False, str(e), max(provider.calls - before, 1))
        raise
    except Exception as e:  # malformed payloads etc.
        record_call(db, provider, False, f"{type(e).__name__}: {e}", max(provider.calls - before, 1))
        raise ProviderError(f"{provider.name}: {type(e).__name__}: {e}") from e
    if provider.calls - before > 0:
        record_call(db, provider, True, calls=provider.calls - before)
    return res


# ----------------------------------------------------------------- securities
def upsert_security(db: Session, info: SecurityInfo, is_sample: bool) -> Security:
    sec = db.scalar(select(Security).where(Security.symbol == info.symbol, Security.exchange == info.exchange))
    if sec is None:
        sec = Security(symbol=info.symbol, exchange=info.exchange)
        db.add(sec)
    sec.name = info.name
    sec.asset_class = info.asset_class
    sec.country = info.country
    sec.currency = info.currency
    sec.sector = info.sector or sec.sector
    sec.industry = info.industry or sec.industry
    sec.cik = info.cik or sec.cik
    sec.description = info.description or sec.description
    sec.meta = {**(sec.meta or {}), **info.meta}
    sec.is_sample = is_sample
    db.flush()
    return sec


def ensure_sample_universe(db: Session, registry: Registry | None = None) -> list[Security]:
    reg = registry or get_registry()
    if not reg.sample_enabled:
        return []
    sample = reg.get("sample")
    return [upsert_security(db, info, True) for info in sample.universe()]  # type: ignore[attr-defined]


def find_security(db: Session, symbol: str, exchange: str | None = None) -> Security | None:
    q = select(Security).where(func.upper(Security.symbol) == symbol.upper())
    if exchange:
        q = q.where(Security.exchange == exchange)
    rows = db.scalars(q.order_by(Security.is_sample.asc())).all()  # prefer real over sample on ties
    return rows[0] if rows else None


def search_securities(db: Session, query: str, include_remote: bool = True, registry: Registry | None = None) -> list[dict]:
    reg = registry or get_registry()
    q = f"%{query.lower()}%"
    local = db.scalars(
        select(Security).where((func.lower(Security.symbol).like(q)) | (func.lower(Security.name).like(q))).limit(25)
    ).all()
    out = [security_dict(s) | {"in_database": True} for s in local]
    seen = {(s.symbol, s.exchange) for s in local}
    remote_errors = []
    if include_remote and len(query) >= 1:
        for p in reg.for_capability("search"):
            try:
                for info in _call(db, p, p.search, query)[:10]:
                    if (info.symbol, info.exchange) in seen:
                        continue
                    seen.add((info.symbol, info.exchange))
                    out.append({"symbol": info.symbol, "name": info.name, "exchange": info.exchange,
                                "asset_class": info.asset_class, "country": info.country, "currency": info.currency,
                                "is_sample": False, "in_database": False, "source": p.name})
                break
            except (ProviderError, NotSupported) as e:
                remote_errors.append(str(e))
    return out[:40]


def add_real_security(db: Session, symbol: str, registry: Registry | None = None) -> Security:
    """Create a real (non-sample) security by looking it up through reference-data providers."""
    reg = registry or get_registry()
    existing = db.scalar(select(Security).where(func.upper(Security.symbol) == symbol.upper(), Security.is_sample.is_(False)))
    if existing:
        return existing
    errors = []
    for p in reg.for_capability("reference"):
        try:
            info = _call(db, p, p.security_info, symbol)
            if info:
                if p.name != "sec_edgar" and "sec_edgar" in reg.providers and reg.get("sec_edgar").is_configured():
                    try:
                        info.cik = reg.get("sec_edgar").cik_for(symbol)  # type: ignore[attr-defined]
                    except ProviderError:
                        pass
                return upsert_security(db, info, False)
        except (ProviderError, NotSupported) as e:
            errors.append(str(e))
    raise DataUnavailable(
        f"Could not resolve '{symbol}' via configured reference-data providers. "
        + ("; ".join(errors) if errors else "No reference-data provider is configured (SEC_USER_AGENT or TIINGO_API_KEY).")
    )


def security_dict(s: Security) -> dict:
    return {"id": s.id, "symbol": s.symbol, "name": s.name, "exchange": s.exchange, "asset_class": s.asset_class,
            "country": s.country, "currency": s.currency, "sector": s.sector, "industry": s.industry,
            "is_sample": s.is_sample, "cik": s.cik, "description": s.description}


# ----------------------------------------------------------------- prices
def refresh_prices(db: Session, sec: Security, registry: Registry | None = None, lookback_years: int = 10) -> dict:
    reg = registry or get_registry()
    last = db.scalar(select(func.max(PriceBar.date)).where(PriceBar.security_id == sec.id))
    end = last_business_day() if sec.is_sample else utcnow().date()
    start = (last + timedelta(days=1)) if last else end - timedelta(days=365 * lookback_years + 5)
    if last and start > end:
        update_quote_from_bars(db, sec)
        return {"security": sec.symbol, "inserted": 0, "source": None, "status": "up_to_date"}
    providers = reg.for_capability("prices", sample=sec.is_sample)
    if not providers:
        raise DataUnavailable("No configured price provider (set TIINGO_API_KEY or ALPHAVANTAGE_API_KEY).")
    errors = []
    for p in providers:
        try:
            bars = _call(db, p, p.daily_bars, sec.symbol, start, end)
        except (ProviderError, NotSupported) as e:
            errors.append(str(e))
            continue
        n = _upsert_bars(db, sec, bars, p)
        update_quote_from_bars(db, sec)
        return {"security": sec.symbol, "inserted": n, "source": p.name, "status": "ok"}
    raise DataUnavailable("; ".join(errors))


def _upsert_bars(db: Session, sec: Security, bars, p: Provider) -> int:
    rows = []
    for b in bars:
        if b.close is None or b.close <= 0:
            continue  # reject non-positive prices
        rows.append({"security_id": sec.id, "date": b.date, "open": b.open, "high": b.high, "low": b.low,
                     "close": b.close, "adj_close": b.adj_close, "volume": b.volume, "source": p.name,
                     "quality": p.quality, "fetched_at": utcnow()})
        if b.dividend:
            db.add(CorporateEvent(security_id=sec.id, kind="dividend", event_date=b.date,
                                  details={"cash": b.dividend}, source=p.name, quality="reported"))
        if b.split_factor and b.split_factor != 1.0:
            db.add(CorporateEvent(security_id=sec.id, kind="split", event_date=b.date,
                                  details={"factor": b.split_factor}, source=p.name, quality="reported"))
    for i in range(0, len(rows), 2000):
        chunk = rows[i : i + 2000]
        stmt = pg_insert(PriceBar).values(chunk)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_bar_security_date",
            set_={k: getattr(stmt.excluded, k) for k in ("open", "high", "low", "close", "adj_close", "volume", "source", "quality", "fetched_at")},
        )
        db.execute(stmt)
    db.flush()
    return len(rows)


def update_quote_from_bars(db: Session, sec: Security) -> Quote | None:
    rows = db.execute(
        select(PriceBar.date, PriceBar.close, PriceBar.source, PriceBar.quality)
        .where(PriceBar.security_id == sec.id).order_by(PriceBar.date.desc()).limit(2)
    ).all()
    if not rows:
        return None
    q = db.get(Quote, sec.id) or Quote(security_id=sec.id)
    q.price = rows[0].close
    q.prev_close = rows[1].close if len(rows) > 1 else None
    q.as_of = datetime.combine(rows[0].date, time(21, 0), tzinfo=timezone.utc)
    q.source = rows[0].source
    q.quality = rows[0].quality
    q.delay_minutes = None
    q.fetched_at = utcnow()
    db.merge(q)
    db.flush()
    return q


def business_days_between(a: date, b: date) -> int:
    if b <= a:
        return 0
    days = 0
    d = a
    while d < b:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days += 1
    return days


def freshness(as_of: datetime | date | None, max_business_days: int) -> dict:
    if as_of is None:
        return {"status": "missing", "age_business_days": None}
    d = as_of.date() if isinstance(as_of, datetime) else as_of
    age = business_days_between(d, last_business_day())
    return {"status": "fresh" if age <= max_business_days else "stale", "age_business_days": age}


def get_quote(db: Session, sec: Security, max_age_days: int = 3) -> dict:
    q = db.get(Quote, sec.id)
    if q is None:
        return {"security_id": sec.id, "symbol": sec.symbol, "price": None, "quality": "unavailable",
                "freshness": freshness(None, max_age_days), "is_sample": sec.is_sample}
    chg = (q.price / q.prev_close - 1) if q.prev_close else None
    return {"security_id": sec.id, "symbol": sec.symbol, "price": q.price, "prev_close": q.prev_close,
            "change_pct": chg, "as_of": q.as_of.isoformat(), "source": q.source, "quality": q.quality,
            "currency": sec.currency, "delay_minutes": q.delay_minutes, "is_sample": sec.is_sample,
            "freshness": freshness(q.as_of, max_age_days)}


def get_bars(db: Session, security_id: int, start: date | None = None, end: date | None = None) -> list[PriceBar]:
    q = select(PriceBar).where(PriceBar.security_id == security_id)
    if start:
        q = q.where(PriceBar.date >= start)
    if end:
        q = q.where(PriceBar.date <= end)
    return list(db.scalars(q.order_by(PriceBar.date)).all())


def close_series(db: Session, security_id: int, start: date | None = None, end: date | None = None) -> tuple[list[date], list[float]]:
    q = select(PriceBar.date, PriceBar.adj_close, PriceBar.close).where(PriceBar.security_id == security_id)
    if start:
        q = q.where(PriceBar.date >= start)
    if end:
        q = q.where(PriceBar.date <= end)
    rows = db.execute(q.order_by(PriceBar.date)).all()
    return [r[0] for r in rows], [float(r[1] if r[1] is not None else r[2]) for r in rows]


def price_on(db: Session, security_id: int, on: date) -> tuple[date, float] | None:
    r = db.execute(select(PriceBar.date, PriceBar.close).where(PriceBar.security_id == security_id, PriceBar.date <= on)
                   .order_by(PriceBar.date.desc()).limit(1)).first()
    return (r[0], float(r[1])) if r else None


# ----------------------------------------------------------------- fundamentals & filings
def refresh_fundamentals(db: Session, sec: Security, registry: Registry | None = None) -> dict:
    reg = registry or get_registry()
    if sec.asset_class != "equity":
        return {"security": sec.symbol, "inserted": 0, "status": "not_applicable"}
    providers = reg.for_capability("fundamentals", sample=sec.is_sample)
    if not providers:
        raise DataUnavailable("No fundamentals provider configured (set SEC_USER_AGENT for SEC EDGAR).")
    errors = []
    for p in providers:
        try:
            facts = _call(db, p, p.fundamentals, sec.symbol, sec.cik)
        except (ProviderError, NotSupported) as e:
            errors.append(str(e))
            continue
        rows = [{"security_id": sec.id, "metric": f.metric, "period_end": f.period_end, "fiscal_year": f.fiscal_year,
                 "fiscal_period": f.fiscal_period, "value": f.value, "unit": f.unit, "source": p.name,
                 "source_ref": f.source_ref, "filed_date": f.filed_date, "quality": f.quality, "fetched_at": utcnow()}
                for f in facts]
        for i in range(0, len(rows), 2000):
            stmt = pg_insert(FundamentalFact).values(rows[i : i + 2000])
            stmt = stmt.on_conflict_do_update(
                constraint="uq_fact",
                set_={k: getattr(stmt.excluded, k) for k in ("value", "unit", "source_ref", "filed_date", "quality", "fetched_at", "fiscal_year")},
            )
            db.execute(stmt)
        db.flush()
        return {"security": sec.symbol, "inserted": len(rows), "source": p.name, "status": "ok"}
    raise DataUnavailable("; ".join(errors))


def get_statements(db: Session, security_id: int, as_of: date | None = None) -> dict:
    """Annual statements as {metric: {period_end_iso: value}} plus provenance.

    ``as_of`` enforces point-in-time correctness: only facts *filed* on or before that date are used.
    """
    q = select(FundamentalFact).where(FundamentalFact.security_id == security_id, FundamentalFact.fiscal_period == "FY")
    if as_of is not None:
        q = q.where(FundamentalFact.filed_date.is_not(None), FundamentalFact.filed_date <= as_of)
    facts = db.scalars(q.order_by(FundamentalFact.period_end)).all()
    stm: dict[str, dict[str, float]] = {}
    prov: dict[str, dict] = {}
    for f in facts:
        stm.setdefault(f.metric, {})[f.period_end.isoformat()] = f.value
        prov[f"{f.metric}|{f.period_end.isoformat()}"] = {"source": f.source, "ref": f.source_ref,
                                                         "filed": f.filed_date.isoformat() if f.filed_date else None,
                                                         "quality": f.quality, "unit": f.unit}
    latest_filed = max((f.filed_date for f in facts if f.filed_date), default=None)
    sources = sorted({f.source for f in facts})
    qualities = sorted({f.quality for f in facts})
    return {"statements": stm, "provenance": prov, "latest_filed": latest_filed.isoformat() if latest_filed else None,
            "sources": sources, "qualities": qualities}


def refresh_filings(db: Session, sec: Security, registry: Registry | None = None) -> dict:
    reg = registry or get_registry()
    providers = reg.for_capability("filings", sample=sec.is_sample)
    if not providers or sec.asset_class != "equity":
        return {"security": sec.symbol, "new": [], "status": "not_available"}
    p = providers[0]
    fl = _call(db, p, p.filings, sec.symbol, sec.cik)
    new = []
    for f in fl:
        stmt = pg_insert(Filing).values(security_id=sec.id, form=f.form, filed_date=f.filed_date, period=f.period,
                                        accession=f.accession, url=f.url, description=f.description, source=p.name,
                                        quality="sample" if sec.is_sample else "reported", fetched_at=utcnow())
        stmt = stmt.on_conflict_do_nothing(constraint="uq_filing").returning(Filing.id)
        if db.execute(stmt).first():
            new.append({"form": f.form, "filed_date": f.filed_date.isoformat(), "accession": f.accession})
    db.flush()
    return {"security": sec.symbol, "new": new, "source": p.name, "status": "ok"}


def refresh_news(db: Session, sec: Security, registry: Registry | None = None) -> dict:
    reg = registry or get_registry()
    providers = reg.for_capability("news", sample=sec.is_sample)
    if not providers:
        return {"security": sec.symbol, "new": 0, "status": "not_available"}
    p = providers[0]
    items = _call(db, p, p.news, sec.symbol)
    n = 0
    for it in items:
        stmt = pg_insert(NewsItem).values(security_id=sec.id, title=it.title, url=it.url, publisher=it.publisher,
                                          published_at=it.published_at, summary=it.summary, sentiment=it.sentiment,
                                          source=p.name, quality="sample" if sec.is_sample else "delayed",
                                          fetched_at=utcnow())
        stmt = stmt.on_conflict_do_nothing(constraint="uq_news_url").returning(NewsItem.id)
        if db.execute(stmt).first():
            n += 1
    db.flush()
    return {"security": sec.symbol, "new": n, "source": p.name, "status": "ok"}


def refresh_events(db: Session, sec: Security, registry: Registry | None = None) -> dict:
    reg = registry or get_registry()
    providers = reg.for_capability("events", sample=sec.is_sample)
    if not providers:
        return {"security": sec.symbol, "status": "not_available"}
    p = providers[0]
    evs = _call(db, p, p.events, sec.symbol)
    today = utcnow().date()
    # replace future events from this provider (they move); keep history
    for old in db.scalars(select(CorporateEvent).where(CorporateEvent.security_id == sec.id, CorporateEvent.source == p.name,
                                                       CorporateEvent.event_date >= today)).all():
        db.delete(old)
    for e in evs:
        db.add(CorporateEvent(security_id=sec.id, kind=e.kind, event_date=e.event_date, details=e.details,
                              source=p.name, quality="sample" if sec.is_sample else "reported"))
    db.flush()
    return {"security": sec.symbol, "events": len(evs), "source": p.name, "status": "ok"}


# ----------------------------------------------------------------- macro & fx
MACRO_SERIES = ["DGS10", "DGS2", "FEDFUNDS", "UNRATE", "VIXCLS", "BAMLH0A0HYM2", "CPI_YOY"]


def refresh_macro(db: Session, registry: Registry | None = None) -> dict:
    reg = registry or get_registry()
    results = {}
    real = reg.for_capability("macro")
    for sid in MACRO_SERIES:
        last = db.scalar(select(func.max(MacroObservation.date)).where(MacroObservation.series_id == sid))
        start = (last - timedelta(days=10)) if last else date(2015, 1, 1)
        provs = [p for p in real if sid != "CPI_YOY"] + ([reg.get("sample")] if reg.sample_enabled else [])
        for p in provs:
            try:
                fred_id = sid
                obs = _call(db, p, p.macro_series, fred_id, start)
            except (ProviderError, NotSupported) as e:
                results[sid] = {"status": "error", "error": str(e), "provider": p.name}
                continue
            if not obs:
                continue
            rows = [{"series_id": sid, "date": d, "value": v, "source": p.name,
                     "quality": "sample" if p.name == "sample" else "reported", "fetched_at": utcnow()} for d, v in obs]
            for i in range(0, len(rows), 3000):
                stmt = pg_insert(MacroObservation).values(rows[i : i + 3000])
                stmt = stmt.on_conflict_do_update(constraint="uq_macro", set_={"value": stmt.excluded.value, "fetched_at": stmt.excluded.fetched_at})
                db.execute(stmt)
            results[sid] = {"status": "ok", "provider": p.name, "observations": len(rows)}
            break
    db.flush()
    return results


def macro_latest(db: Session, series_id: str) -> dict | None:
    """Latest observation, preferring real sources over sample."""
    rows = db.execute(select(MacroObservation).where(MacroObservation.series_id == series_id)
                      .order_by(MacroObservation.date.desc()).limit(400)).scalars().all()
    if not rows:
        return None
    real = [r for r in rows if r.quality != "sample"]
    use = real or rows
    r = use[0]
    return {"series_id": series_id, "date": r.date.isoformat(), "value": r.value, "source": r.source, "quality": r.quality}


def macro_series(db: Session, series_id: str, start: date) -> tuple[list[date], list[float], str]:
    rows = db.execute(select(MacroObservation).where(MacroObservation.series_id == series_id, MacroObservation.date >= start)
                      .order_by(MacroObservation.date)).scalars().all()
    real = [r for r in rows if r.quality != "sample"]
    use = real or rows
    q = "reported" if real else ("sample" if rows else "unavailable")
    return [r.date for r in use], [r.value for r in use], q


FX_CCYS = ["EUR", "GBP", "JPY", "CHF", "CAD", "AUD"]


def refresh_fx(db: Session, registry: Registry | None = None) -> dict:
    reg = registry or get_registry()
    last = db.scalar(select(func.max(FxRate.date)).where(FxRate.source != "sample"))
    end = utcnow().date()
    start = (last - timedelta(days=5)) if last else end - timedelta(days=365 * 5)
    out = {}
    for p in reg.for_capability("fx"):
        try:
            series = _call(db, p, p.fx_series, "USD", FX_CCYS, start, end)
            out[p.name] = _store_fx(db, "USD", series, p.name, "reference")
            break
        except (ProviderError, NotSupported) as e:
            out[p.name] = f"error: {e}"
    if reg.sample_enabled:
        last_s = db.scalar(select(func.max(FxRate.date)).where(FxRate.source == "sample"))
        s_start = (last_s + timedelta(days=1)) if last_s else date(2015, 1, 1)
        series = reg.get("sample").fx_series("USD", ["EUR", "GBP", "JPY"], s_start, end)
        out["sample"] = _store_fx(db, "USD", series, "sample", "sample")
    db.flush()
    return out


def _store_fx(db: Session, base: str, series: dict, source: str, quality: str) -> int:
    rows = [{"base": base, "quote": q, "date": d, "rate": v, "source": source, "quality": quality, "fetched_at": utcnow()}
            for q, obs in series.items() for d, v in obs]
    for i in range(0, len(rows), 3000):
        stmt = pg_insert(FxRate).values(rows[i : i + 3000])
        stmt = stmt.on_conflict_do_nothing(constraint="uq_fx")
        db.execute(stmt)
    return len(rows)


def fx_rate(db: Session, from_ccy: str, to_ccy: str, on: date | None = None, allow_sample: bool = True) -> dict | None:
    """Rate converting 1 unit of ``from_ccy`` into ``to_ccy`` using USD-based tables. Prefers real reference data."""
    if from_ccy == to_ccy:
        return {"rate": 1.0, "date": None, "source": "identity", "quality": "exact"}
    on = on or utcnow().date()

    def usd_to(ccy: str):
        if ccy == "USD":
            return (1.0, None, "identity", "exact")
        for src_filter in ([FxRate.source != "sample"], [FxRate.source == "sample"] if allow_sample else None):
            if src_filter is None:
                continue
            r = db.execute(select(FxRate).where(FxRate.base == "USD", FxRate.quote == ccy, FxRate.date <= on, *src_filter)
                           .order_by(FxRate.date.desc()).limit(1)).scalars().first()
            if r:
                return (r.rate, r.date, r.source, r.quality)
        return None

    a, b = usd_to(from_ccy), usd_to(to_ccy)
    if a is None or b is None:
        return None
    rate = b[0] / a[0]  # 1 from = (1/a) USD = b/a to
    dates = [x for x in (a[1], b[1]) if x]
    quality = "sample" if "sample" in (a[3], b[3]) else "reference"
    return {"rate": rate, "date": min(dates).isoformat() if dates else None, "source": f"{a[2]}/{b[2]}", "quality": quality}
