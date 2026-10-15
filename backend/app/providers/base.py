"""Provider adapter interface.

Adapters translate a vendor API into neutral dataclasses. They never write to the database; the
``MarketDataService`` persists results with source attribution. Each adapter declares its capabilities, the
quality label of its data (eod / delayed / reported / reference / sample), and its licensing notes.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, ClassVar

import httpx

log = logging.getLogger(__name__)


class ProviderError(Exception):
    """Vendor call failed (network, HTTP status, malformed payload, vendor-side rate limit)."""


class ProviderNotConfigured(ProviderError):
    pass


class ProviderQuotaExceeded(ProviderError):
    pass


class NotSupported(ProviderError):
    pass


@dataclass
class SecurityInfo:
    symbol: str
    name: str
    exchange: str = ""
    asset_class: str = "equity"
    country: str | None = None
    currency: str = "USD"
    sector: str | None = None
    industry: str | None = None
    cik: str | None = None
    description: str | None = None
    meta: dict[str, Any] = field(default_factory=dict)


@dataclass
class Bar:
    date: date
    close: float
    open: float | None = None
    high: float | None = None
    low: float | None = None
    adj_close: float | None = None
    volume: float | None = None
    dividend: float | None = None
    split_factor: float | None = None


@dataclass
class Fact:
    metric: str
    period_end: date
    value: float
    fiscal_year: int | None = None
    fiscal_period: str = "FY"
    unit: str = "USD"
    source_ref: str | None = None
    filed_date: date | None = None
    quality: str = "reported"


@dataclass
class FilingInfo:
    form: str
    filed_date: date
    accession: str
    period: date | None = None
    url: str | None = None
    description: str | None = None


@dataclass
class NewsInfo:
    title: str
    url: str
    published_at: datetime
    publisher: str | None = None
    summary: str | None = None
    sentiment: float | None = None
    symbols: list[str] = field(default_factory=list)


@dataclass
class EventInfo:
    kind: str
    event_date: date
    details: dict[str, Any] = field(default_factory=dict)


class Provider:
    name: ClassVar[str] = "base"
    title: ClassVar[str] = ""
    capabilities: ClassVar[set[str]] = set()
    quality: ClassVar[str] = "eod"
    requires_key: ClassVar[bool] = True
    daily_limit: ClassVar[int | None] = None
    min_interval_seconds: ClassVar[float] = 0.0
    homepage: ClassVar[str] = ""
    licensing: ClassVar[str] = ""
    setup: ClassVar[str] = ""

    _lock: ClassVar[threading.Lock]
    _last_call: ClassVar[float]

    def __init__(self, timeout: float = 20.0, transport: httpx.BaseTransport | None = None):
        self.timeout = timeout
        self._transport = transport
        self.calls = 0  # calls made by this instance (for usage accounting)
        if not hasattr(type(self), "_lock"):
            type(self)._lock = threading.Lock()
            type(self)._last_call = 0.0

    # ---- configuration
    def is_configured(self) -> bool:
        return True

    def status_note(self) -> str:
        return "" if self.is_configured() else self.setup

    # ---- capabilities (override as supported)
    def search(self, query: str) -> list[SecurityInfo]:
        raise NotSupported(f"{self.name} does not support search")

    def security_info(self, symbol: str) -> SecurityInfo | None:
        raise NotSupported(f"{self.name} does not support reference data")

    def daily_bars(self, symbol: str, start: date, end: date) -> list[Bar]:
        raise NotSupported(f"{self.name} does not support prices")

    def fundamentals(self, symbol: str, cik: str | None = None) -> list[Fact]:
        raise NotSupported(f"{self.name} does not support fundamentals")

    def filings(self, symbol: str, cik: str | None = None, limit: int = 40) -> list[FilingInfo]:
        raise NotSupported(f"{self.name} does not support filings")

    def news(self, symbol: str, limit: int = 20) -> list[NewsInfo]:
        raise NotSupported(f"{self.name} does not support news")

    def events(self, symbol: str) -> list[EventInfo]:
        raise NotSupported(f"{self.name} does not support events")

    def macro_series(self, series_id: str, start: date) -> list[tuple[date, float]]:
        raise NotSupported(f"{self.name} does not support macro data")

    def fx_series(self, base: str, quotes: list[str], start: date, end: date) -> dict[str, list[tuple[date, float]]]:
        raise NotSupported(f"{self.name} does not support FX")

    # ---- HTTP plumbing
    def _client(self, headers: dict[str, str] | None = None) -> httpx.Client:
        return httpx.Client(timeout=self.timeout, headers=headers or {}, transport=self._transport,
                            follow_redirects=True)

    def _throttle(self) -> None:
        if self.min_interval_seconds <= 0:
            return
        cls = type(self)
        with cls._lock:
            wait = cls._last_call + self.min_interval_seconds - time.monotonic()
            if wait > 0:
                time.sleep(wait)
            cls._last_call = time.monotonic()

    def _get(self, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None,
             expect: str = "json") -> Any:
        if not self.is_configured():
            raise ProviderNotConfigured(f"{self.name} is not configured: {self.setup}")
        self._throttle()
        self.calls += 1
        try:
            with self._client(headers) as c:
                r = c.get(url, params=params)
        except httpx.HTTPError as e:
            raise ProviderError(f"{self.name}: network error: {type(e).__name__}: {e}") from e
        if r.status_code == 429:
            raise ProviderQuotaExceeded(f"{self.name}: HTTP 429 rate limited")
        if r.status_code >= 400:
            raise ProviderError(f"{self.name}: HTTP {r.status_code} for {_redact(str(r.request.url))}")
        if expect == "json":
            try:
                return r.json()
            except ValueError as e:
                raise ProviderError(f"{self.name}: response was not JSON (first bytes: {r.text[:80]!r})") from e
        return r.text


def _redact(url: str) -> str:
    """Never log secrets embedded in query strings."""
    import re

    return re.sub(r"(?i)(token|apikey|api_key|key)=[^&]+", r"\1=***", url)


def parse_date(s: str) -> date:
    return date.fromisoformat(s[:10])
