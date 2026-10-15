"""Database models.

Conventions
-----------
* Every market/fundamental datum carries ``source`` and a ``quality`` label:
  ``live`` | ``delayed`` | ``eod`` | ``reported`` | ``manual`` | ``sample`` | ``derived``.
  ``sample`` rows are synthetic and must never be shown without a SAMPLE label.
* Monetary amounts on transactions are ``Numeric`` (exact decimals). Analytics use floats.
* All user-owned rows carry ``user_id`` and every query in the API layer filters on it.
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, utcnow

Money = Numeric(28, 10)


def _ts() -> Mapped[datetime]:
    return mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)


# ---------------------------------------------------------------- users & audit
class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    display_name: Mapped[str] = mapped_column(String(120), default="")
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    failed_logins: Mapped[int] = mapped_column(Integer, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    preferences: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    actor: Mapped[str] = mapped_column(String(32), default="user")  # user | agent | system | worker
    action: Mapped[str] = mapped_column(String(80), index=True)
    entity_type: Mapped[str | None] = mapped_column(String(60))
    entity_id: Mapped[str | None] = mapped_column(String(60))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    ip: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- reference & market data
class Security(Base):
    __tablename__ = "securities"
    __table_args__ = (UniqueConstraint("symbol", "exchange", name="uq_security_symbol_exchange"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), index=True)
    name: Mapped[str] = mapped_column(String(200))
    exchange: Mapped[str] = mapped_column(String(32), default="")
    asset_class: Mapped[str] = mapped_column(String(24), default="equity")  # equity etf index fx commodity bond crypto
    country: Mapped[str | None] = mapped_column(String(2))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    sector: Mapped[str | None] = mapped_column(String(80))
    industry: Mapped[str | None] = mapped_column(String(120))
    cik: Mapped[str | None] = mapped_column(String(10), index=True)
    is_sample: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    description: Mapped[str | None] = mapped_column(Text)
    meta: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class PriceBar(Base):
    __tablename__ = "price_bars"
    __table_args__ = (UniqueConstraint("security_id", "date", name="uq_bar_security_date"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date, index=True)
    open: Mapped[float | None] = mapped_column(Float)
    high: Mapped[float | None] = mapped_column(Float)
    low: Mapped[float | None] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    adj_close: Mapped[float | None] = mapped_column(Float)
    volume: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(16), default="eod")
    fetched_at: Mapped[datetime] = _ts()


class Quote(Base):
    __tablename__ = "quotes"
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), primary_key=True)
    price: Mapped[float] = mapped_column(Float)
    prev_close: Mapped[float | None] = mapped_column(Float)
    as_of: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(16))  # live delayed eod sample
    delay_minutes: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = _ts()


class FundamentalFact(Base):
    """Long-format financial statement facts (one metric per row) with full provenance."""

    __tablename__ = "fundamental_facts"
    __table_args__ = (
        UniqueConstraint("security_id", "metric", "period_end", "fiscal_period", "source", name="uq_fact"),
        Index("ix_fact_sec_metric", "security_id", "metric"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"))
    metric: Mapped[str] = mapped_column(String(60))
    period_end: Mapped[date] = mapped_column(Date)
    fiscal_year: Mapped[int | None] = mapped_column(Integer)
    fiscal_period: Mapped[str] = mapped_column(String(4), default="FY")  # FY Q1 Q2 Q3 Q4
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16), default="USD")
    source: Mapped[str] = mapped_column(String(40))
    source_ref: Mapped[str | None] = mapped_column(String(200))  # e.g. accession number / XBRL tag
    filed_date: Mapped[date | None] = mapped_column(Date)
    quality: Mapped[str] = mapped_column(String(16), default="reported")
    fetched_at: Mapped[datetime] = _ts()


class Filing(Base):
    __tablename__ = "filings"
    __table_args__ = (UniqueConstraint("security_id", "accession", name="uq_filing"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), index=True)
    form: Mapped[str] = mapped_column(String(20))
    filed_date: Mapped[date] = mapped_column(Date, index=True)
    period: Mapped[date | None] = mapped_column(Date)
    accession: Mapped[str] = mapped_column(String(40))
    url: Mapped[str | None] = mapped_column(String(500))
    description: Mapped[str | None] = mapped_column(String(300))
    source: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(16), default="reported")
    fetched_at: Mapped[datetime] = _ts()


class CorporateEvent(Base):
    __tablename__ = "corporate_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(24))  # earnings dividend split
    event_date: Mapped[date] = mapped_column(Date, index=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    source: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(16), default="reported")


class NewsItem(Base):
    __tablename__ = "news_items"
    __table_args__ = (UniqueConstraint("url", name="uq_news_url"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000))
    publisher: Mapped[str | None] = mapped_column(String(200))
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    summary: Mapped[str | None] = mapped_column(Text)  # UNTRUSTED external text
    sentiment: Mapped[float | None] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(16), default="delayed")
    fetched_at: Mapped[datetime] = _ts()


class MacroObservation(Base):
    __tablename__ = "macro_observations"
    __table_args__ = (UniqueConstraint("series_id", "date", "source", name="uq_macro"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    series_id: Mapped[str] = mapped_column(String(40), index=True)
    date: Mapped[date] = mapped_column(Date)
    value: Mapped[float] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(16), default="reported")
    fetched_at: Mapped[datetime] = _ts()


class FxRate(Base):
    __tablename__ = "fx_rates"
    __table_args__ = (UniqueConstraint("base", "quote", "date", "source", name="uq_fx"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    base: Mapped[str] = mapped_column(String(3))
    quote: Mapped[str] = mapped_column(String(3))
    date: Mapped[date] = mapped_column(Date, index=True)
    rate: Mapped[float] = mapped_column(Float)  # 1 base = rate quote
    source: Mapped[str] = mapped_column(String(40))
    quality: Mapped[str] = mapped_column(String(16), default="reference")
    fetched_at: Mapped[datetime] = _ts()


class ProviderStatus(Base):
    __tablename__ = "provider_status"
    provider: Mapped[str] = mapped_column(String(40), primary_key=True)
    configured: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="unknown")  # ok degraded down unconfigured unknown
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error: Mapped[str | None] = mapped_column(Text)
    calls_today: Mapped[int] = mapped_column(Integer, default=0)
    calls_date: Mapped[date | None] = mapped_column(Date)
    updated_at: Mapped[datetime] = _ts()


class ApiUsage(Base):
    __tablename__ = "api_usage"
    __table_args__ = (UniqueConstraint("provider", "day", "user_id", name="uq_usage"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(40))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    day: Mapped[date] = mapped_column(Date)
    calls: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    est_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)


# ---------------------------------------------------------------- watchlists & portfolios
class Watchlist(Base):
    __tablename__ = "watchlists"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = _ts()
    items: Mapped[list[WatchlistItem]] = relationship(cascade="all, delete-orphan", lazy="selectin")


class WatchlistItem(Base):
    __tablename__ = "watchlist_items"
    __table_args__ = (UniqueConstraint("watchlist_id", "security_id", name="uq_watch_item"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    watchlist_id: Mapped[int] = mapped_column(ForeignKey("watchlists.id", ondelete="CASCADE"))
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"))
    note: Mapped[str | None] = mapped_column(Text)
    added_by: Mapped[str] = mapped_column(String(16), default="user")
    created_at: Mapped[datetime] = _ts()


class Portfolio(Base):
    __tablename__ = "portfolios"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    # long_term | trading | research | paper | manual | live
    kind: Mapped[str] = mapped_column(String(16), default="manual")
    base_currency: Mapped[str] = mapped_column(String(3), default="USD")
    benchmark_security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id", ondelete="SET NULL"))
    is_hypothetical: Mapped[bool] = mapped_column(Boolean, default=True)
    cost_basis_method: Mapped[str] = mapped_column(String(8), default="FIFO")  # FIFO | AVG
    description: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class Transaction(Base):
    __tablename__ = "transactions"
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    # buy sell dividend interest fee deposit withdrawal split
    type: Mapped[str] = mapped_column(String(16))
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id", ondelete="RESTRICT"))
    trade_date: Mapped[date] = mapped_column(Date, index=True)
    quantity: Mapped[Decimal | None] = mapped_column(Money)
    price: Mapped[Decimal | None] = mapped_column(Money)
    amount: Mapped[Decimal | None] = mapped_column(Money)  # cash amount for dividends/fees/deposits
    fees: Mapped[Decimal] = mapped_column(Money, default=Decimal(0))
    currency: Mapped[str] = mapped_column(String(3), default="USD")
    fx_rate: Mapped[Decimal] = mapped_column(Money, default=Decimal(1))  # 1 unit txn currency = fx_rate base
    split_ratio: Mapped[Decimal | None] = mapped_column(Money)
    notes: Mapped[str | None] = mapped_column(Text)
    origin: Mapped[str] = mapped_column(String(16), default="manual")  # manual import agent_paper
    created_at: Mapped[datetime] = _ts()
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PortfolioSnapshot(Base):
    __tablename__ = "portfolio_snapshots"
    __table_args__ = (UniqueConstraint("portfolio_id", "date", name="uq_snapshot"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    portfolio_id: Mapped[int] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), index=True)
    date: Mapped[date] = mapped_column(Date)
    market_value: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    net_flows: Mapped[float] = mapped_column(Float, default=0.0)
    data_quality: Mapped[str] = mapped_column(String(16))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class RiskPolicy(Base):
    __tablename__ = "risk_policies"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"), unique=True)
    max_position_pct: Mapped[float] = mapped_column(Float, default=0.10)
    max_sector_pct: Mapped[float] = mapped_column(Float, default=0.30)
    max_country_pct: Mapped[float] = mapped_column(Float, default=0.70)
    max_drawdown_pct: Mapped[float] = mapped_column(Float, default=0.20)
    max_annual_vol: Mapped[float] = mapped_column(Float, default=0.25)
    min_avg_dollar_volume: Mapped[float] = mapped_column(Float, default=1_000_000.0)
    max_pct_of_adv: Mapped[float] = mapped_column(Float, default=0.05)
    stale_price_days: Mapped[int] = mapped_column(Integer, default=5)
    stale_fundamentals_days: Mapped[int] = mapped_column(Integer, default=200)
    max_pairwise_corr: Mapped[float] = mapped_column(Float, default=0.85)
    updated_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- alerts & notifications
class Alert(Base):
    __tablename__ = "alerts"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    # price_above price_below pct_move valuation filing earnings dividend news thesis concentration drawdown data_feed research_task
    kind: Mapped[str] = mapped_column(String(24))
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"))
    portfolio_id: Mapped[int | None] = mapped_column(ForeignKey("portfolios.id", ondelete="CASCADE"))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    channels: Mapped[list[str]] = mapped_column(JSON, default=lambda: ["in_app"])
    cooldown_minutes: Mapped[int] = mapped_column(Integer, default=24 * 60)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    last_triggered_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class Notification(Base):
    __tablename__ = "notifications"
    __table_args__ = (Index("ix_notif_user_read", "user_id", "read_at"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    title: Mapped[str] = mapped_column(String(300))
    body: Mapped[str] = mapped_column(Text, default="")
    severity: Mapped[str] = mapped_column(String(12), default="info")  # info warning critical
    category: Mapped[str] = mapped_column(String(32), default="general")
    link: Mapped[str | None] = mapped_column(String(300))
    dedupe_key: Mapped[str | None] = mapped_column(String(200), index=True)
    read_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    delivered_channels: Mapped[list[str]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- research
class ScreenDefinition(Base):
    __tablename__ = "screen_definitions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    strategy_key: Mapped[str | None] = mapped_column(String(40))
    criteria: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    filters: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    run_in_agent: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class ResearchRun(Base):
    __tablename__ = "research_runs"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    job_run_id: Mapped[int | None] = mapped_column(ForeignKey("job_runs.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(32))  # research_cycle screen manual
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = _ts()


class ResearchCandidate(Base):
    __tablename__ = "research_candidates"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("research_runs.id", ondelete="CASCADE"), index=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"))
    strategy_key: Mapped[str] = mapped_column(String(40))
    strategy_version: Mapped[str | None] = mapped_column(String(20))
    horizon: Mapped[str] = mapped_column(String(12), default="long")  # long | short
    score: Mapped[float] = mapped_column(Float)
    rank: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), default="new")  # new reviewing dismissed promoted
    explanation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data_quality: Mapped[str] = mapped_column(String(16), default="unknown")
    created_at: Mapped[datetime] = _ts()


class ResearchReport(Base):
    __tablename__ = "research_reports"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"), index=True)
    horizon: Mapped[str] = mapped_column(String(12), default="long")
    title: Mapped[str] = mapped_column(String(300))
    # consider | monitor | reject | insufficient_evidence
    conclusion: Mapped[str] = mapped_column(String(24))
    confidence: Mapped[float | None] = mapped_column(Float)  # calibrated 0..1 or null when unknown
    sections: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    sources: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    calculations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    data_quality: Mapped[str] = mapped_column(String(16))
    narrative_by: Mapped[str] = mapped_column(String(40), default="deterministic")  # deterministic | <llm model>
    model_version: Mapped[str] = mapped_column(String(40))
    created_by: Mapped[str] = mapped_column(String(16), default="user")
    created_at: Mapped[datetime] = _ts()


class AgentMemory(Base):
    __tablename__ = "agent_memory"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # research decision lesson preference fact
    content: Mapped[str] = mapped_column(Text)
    tags: Mapped[list[str]] = mapped_column(JSON, default=list)
    security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id", ondelete="SET NULL"))
    ref_type: Mapped[str | None] = mapped_column(String(32))
    ref_id: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = _ts()


class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(200), default="New conversation")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = _ts()


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user assistant
    content: Mapped[str] = mapped_column(Text)
    tool_calls: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    # raw provider blocks needed to continue the conversation append-only
    raw: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    engine: Mapped[str] = mapped_column(String(40), default="")
    created_at: Mapped[datetime] = _ts()


class PendingAction(Base):
    """Actions the agent proposes that need explicit user authorization (watchlist/paper-portfolio changes)."""

    __tablename__ = "pending_actions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    action_type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    rationale: Mapped[str] = mapped_column(Text, default="")
    risk_check: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending approved rejected executed failed
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


# ---------------------------------------------------------------- learning & evaluation
class StrategyVersion(Base):
    __tablename__ = "strategy_versions"
    __table_args__ = (UniqueConstraint("user_id", "strategy_key", "version", name="uq_strategy_version"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy_key: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(20))
    params: Mapped[dict[str, Any]] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(16), default="candidate")  # production candidate retired rejected
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id", ondelete="SET NULL"))
    notes: Mapped[str | None] = mapped_column(Text)
    proposed_by: Mapped[str] = mapped_column(String(16), default="user")
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


class Prediction(Base):
    __tablename__ = "predictions"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    security_id: Mapped[int] = mapped_column(ForeignKey("securities.id", ondelete="CASCADE"))
    benchmark_security_id: Mapped[int | None] = mapped_column(ForeignKey("securities.id", ondelete="SET NULL"))
    kind: Mapped[str] = mapped_column(String(24), default="direction")  # direction | outperform
    direction: Mapped[str] = mapped_column(String(8))  # up | down
    probability: Mapped[float] = mapped_column(Float)  # stated probability the direction is right
    horizon_days: Mapped[int] = mapped_column(Integer)
    start_date: Mapped[date] = mapped_column(Date)
    evaluate_on: Mapped[date] = mapped_column(Date, index=True)
    baseline_price: Mapped[float] = mapped_column(Float)
    baseline_benchmark: Mapped[float | None] = mapped_column(Float)
    rationale: Mapped[str] = mapped_column(Text, default="")
    assumptions: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    data_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    evaluation_criteria: Mapped[str] = mapped_column(Text, default="")
    model_version: Mapped[str] = mapped_column(String(40))
    source: Mapped[str] = mapped_column(String(16), default="user")  # user agent strategy
    data_quality: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), default="open")  # open evaluated void
    outcome: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()
    evaluated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Backtest(Base):
    __tablename__ = "backtests"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy_key: Mapped[str] = mapped_column(String(40))
    strategy_version_id: Mapped[int | None] = mapped_column(ForeignKey("strategy_versions.id", ondelete="SET NULL"))
    config: Mapped[dict[str, Any]] = mapped_column(JSON)
    metrics: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    equity_curve: Mapped[list[Any]] = mapped_column(JSON, default=list)
    folds: Mapped[list[Any]] = mapped_column(JSON, default=list)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    data_quality: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(12), default="done")
    created_at: Mapped[datetime] = _ts()


class Experiment(Base):
    __tablename__ = "experiments"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    strategy_key: Mapped[str] = mapped_column(String(40))
    baseline_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id", ondelete="CASCADE"))
    candidate_version_id: Mapped[int] = mapped_column(ForeignKey("strategy_versions.id", ondelete="CASCADE"))
    acceptance_criteria: Mapped[dict[str, Any]] = mapped_column(JSON)
    results: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # pending passed failed promoted rejected
    status: Mapped[str] = mapped_column(String(12), default="pending")
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = _ts()


# ---------------------------------------------------------------- scheduler / queue
class AgentSettings(Base):
    __tablename__ = "agent_settings"
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    state: Mapped[str] = mapped_column(String(12), default="running")  # running paused stopped
    timezone: Mapped[str] = mapped_column(String(64), default="UTC")
    operating_start_hour: Mapped[int] = mapped_column(Integer, default=0)
    operating_end_hour: Mapped[int] = mapped_column(Integer, default=24)
    operating_weekdays_only: Mapped[bool] = mapped_column(Boolean, default=False)
    daily_llm_budget_usd: Mapped[float] = mapped_column(Float, default=2.0)
    monthly_llm_budget_usd: Mapped[float] = mapped_column(Float, default=30.0)
    daily_provider_call_budget: Mapped[int] = mapped_column(Integer, default=500)
    max_reports_per_cycle: Mapped[int] = mapped_column(Integer, default=3)
    notify_min_severity: Mapped[str] = mapped_column(String(12), default="info")
    updated_at: Mapped[datetime] = _ts()


class ScheduledJob(Base):
    __tablename__ = "scheduled_jobs"
    __table_args__ = (UniqueConstraint("user_id", "job_type", "name", name="uq_sched_job"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    job_type: Mapped[str] = mapped_column(String(40))
    cron: Mapped[str] = mapped_column(String(64))  # standard 5-field cron, UTC
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=600)
    next_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()


class JobRun(Base):
    __tablename__ = "job_runs"
    __table_args__ = (Index("ix_jobrun_status_after", "status", "run_after"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    scheduled_job_id: Mapped[int | None] = mapped_column(ForeignKey("scheduled_jobs.id", ondelete="SET NULL"))
    job_type: Mapped[str] = mapped_column(String(40))
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    trigger: Mapped[str] = mapped_column(String(16), default="schedule")  # schedule manual event retry
    # queued running succeeded failed cancelled
    status: Mapped[str] = mapped_column(String(12), default="queued")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=3)
    timeout_seconds: Mapped[int] = mapped_column(Integer, default=600)
    run_after: Mapped[datetime] = _ts()
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    worker_id: Mapped[str | None] = mapped_column(String(80))
    error: Mapped[str | None] = mapped_column(Text)
    error_history: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    log: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = _ts()


class WorkerHeartbeat(Base):
    __tablename__ = "worker_heartbeats"
    worker_id: Mapped[str] = mapped_column(String(80), primary_key=True)
    hostname: Mapped[str] = mapped_column(String(120))
    started_at: Mapped[datetime] = _ts()
    last_seen_at: Mapped[datetime] = _ts()
    jobs_processed: Mapped[int] = mapped_column(Integer, default=0)
    version: Mapped[str] = mapped_column(String(40), default="")
