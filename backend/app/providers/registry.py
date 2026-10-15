"""Provider registry: builds adapters from settings and routes requests by capability.

Routing rule: securities flagged ``is_sample`` are served only by the sample provider; real securities are
never served by the sample provider. This guarantees synthetic data cannot leak into real instruments.
"""
from __future__ import annotations

from functools import lru_cache

from app.config import Settings, get_settings
from app.providers.base import Provider
from app.providers.sample import SampleProvider
from app.providers.sec_edgar import SecEdgarProvider
from app.providers.vendors import AlphaVantageProvider, FrankfurterProvider, FredProvider, TiingoProvider

# Priority per capability for REAL instruments (first configured provider wins).
PRIORITY = {
    "prices": ["tiingo", "alphavantage"],
    "search": ["tiingo", "sec_edgar", "alphavantage"],
    "reference": ["sec_edgar", "tiingo"],
    "fundamentals": ["sec_edgar"],
    "filings": ["sec_edgar"],
    "news": ["alphavantage"],
    "events": ["alphavantage"],
    "macro": ["fred"],
    "fx": ["frankfurter"],
}


def _secret(v) -> str | None:
    return v.get_secret_value() if v is not None else None


class Registry:
    def __init__(self, settings: Settings, transport=None):
        t = settings.provider_timeout_seconds
        self.providers: dict[str, Provider] = {
            "sample": SampleProvider(timeout=t, transport=transport),
            "sec_edgar": SecEdgarProvider(settings.sec_user_agent, timeout=t, transport=transport),
            "tiingo": TiingoProvider(_secret(settings.tiingo_api_key), timeout=t, transport=transport),
            "alphavantage": AlphaVantageProvider(_secret(settings.alphavantage_api_key), timeout=t, transport=transport),
            "fred": FredProvider(_secret(settings.fred_api_key), timeout=t, transport=transport),
            "frankfurter": FrankfurterProvider(settings.enable_frankfurter, timeout=t, transport=transport),
        }
        self.sample_enabled = settings.enable_sample_data

    def get(self, name: str) -> Provider:
        return self.providers[name]

    def for_capability(self, capability: str, sample: bool = False) -> list[Provider]:
        if sample:
            return [self.providers["sample"]] if self.sample_enabled else []
        return [self.providers[n] for n in PRIORITY.get(capability, []) if self.providers[n].is_configured()]

    def describe(self) -> list[dict]:
        out = []
        for name, p in self.providers.items():
            out.append({
                "name": name, "title": p.title, "capabilities": sorted(p.capabilities), "quality": p.quality,
                "requires_key": p.requires_key,
                "configured": (p.is_configured() if name != "sample" else self.sample_enabled),
                "daily_limit": p.daily_limit, "homepage": p.homepage, "licensing": p.licensing, "setup": p.setup,
            })
        return out


@lru_cache
def get_registry() -> Registry:
    return Registry(get_settings())
