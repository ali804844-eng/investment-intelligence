"""Environment-based configuration. Secrets are read from the environment only and never sent to clients."""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = Field("development", description="development | test | production")
    app_name: str = "Investment Intelligence"
    database_url: str = "postgresql+psycopg://ii:ii_dev_password@localhost:5432/ii"
    # Used to sign session tokens. MUST be overridden in production.
    secret_key: SecretStr = SecretStr("dev-only-insecure-secret-change-me")
    access_token_minutes: int = 60 * 12
    cookie_secure: bool = False
    cors_origins: str = "http://localhost:5173"
    allow_registration: bool = True

    # --- AI ---
    anthropic_api_key: SecretStr | None = None
    anthropic_model: str = "claude-opus-5-5"
    anthropic_effort: str = "medium"
    # USD per 1M tokens (used for cost estimates only)
    anthropic_input_price: float = 4.0
    anthropic_output_price: float = 20.0

    # --- Data providers ---
    sec_user_agent: str | None = Field(None, description="'Name contact@email' required by SEC fair-access policy")
    tiingo_api_key: SecretStr | None = None
    alphavantage_api_key: SecretStr | None = None
    fred_api_key: SecretStr | None = None
    enable_frankfurter: bool = True
    enable_sample_data: bool = True
    provider_timeout_seconds: float = 20.0

    # --- Notifications ---
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None

    # --- Worker ---
    worker_poll_seconds: float = 5.0
    worker_stale_after_seconds: int = 900

    # --- Trading safety ---
    # Live order execution is not implemented. This flag exists so the gate is explicit and auditable.
    live_trading_enabled: bool = False

    @property
    def is_production(self) -> bool:
        return self.app_env == "production"

    def validate_for_production(self) -> list[str]:
        problems = []
        if self.secret_key.get_secret_value().startswith("dev-only"):
            problems.append("SECRET_KEY must be set to a strong random value")
        if not self.cookie_secure:
            problems.append("COOKIE_SECURE should be true behind HTTPS")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
