"""Environment-driven settings.

Only *bootstrap* configuration lives here (things the app needs before it can
talk to a database). Everything an operator can change at runtime - the LLM
provider, API keys, external datastores - is managed from the admin UI and
persisted in Postgres or in APP_DATA_DIR.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- Application -------------------------------------------------------
    app_name: str = Field(default="FareLens", alias="APP_NAME")
    app_env: Literal["production", "development"] = Field(default="production", alias="APP_ENV")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    host: str = Field(default="0.0.0.0", alias="HOST")
    port: int = Field(default=8000, alias="PORT")
    app_secret_key: SecretStr = Field(default=SecretStr(""), alias="APP_SECRET_KEY")
    app_data_dir: str = Field(default="./data", alias="APP_DATA_DIR")
    prompts_dir: str = Field(default="prompts", alias="PROMPTS_DIR")
    cors_allow_origins: str = Field(default="*", alias="CORS_ALLOW_ORIGINS")

    # --- Bundled datastores ----------------------------------------------
    database_url: SecretStr = Field(
        default=SecretStr("postgresql://farelens:farelens@localhost:5432/farelens"),
        alias="DATABASE_URL",
    )
    redis_url: SecretStr = Field(default=SecretStr("redis://localhost:6379/0"), alias="REDIS_URL")
    postgres_pool_min: int = Field(default=1, alias="POSTGRES_POOL_MIN")
    postgres_pool_max: int = Field(default=10, alias="POSTGRES_POOL_MAX")

    # --- Admin -------------------------------------------------------------
    admin_default_username: str = Field(default="admin", alias="ADMIN_DEFAULT_USERNAME")
    admin_default_password: SecretStr = Field(default=SecretStr("admin"), alias="ADMIN_DEFAULT_PASSWORD")
    admin_session_hours: int = Field(default=12, alias="ADMIN_SESSION_HOURS")

    # --- Limits & caching ---------------------------------------------------
    max_fare_rules_chars: int = Field(default=60000, alias="MAX_FARE_RULES_CHARS")
    max_history_messages: int = Field(default=20, alias="MAX_HISTORY_MESSAGES")
    summary_cache_ttl_seconds: int = Field(default=2592000, alias="SUMMARY_CACHE_TTL_SECONDS")
    summary_cache_namespace: str = Field(default="v1", alias="SUMMARY_CACHE_NAMESPACE")
    conversation_ttl_seconds: int = Field(default=3600, alias="CONVERSATION_TTL_SECONDS")
    max_cached_conversations: int = Field(default=10000, alias="MAX_CACHED_CONVERSATIONS")
    llm_timeout_seconds: int = Field(default=90, alias="LLM_TIMEOUT_SECONDS")
    rate_limit_requests: int = Field(default=120, alias="RATE_LIMIT_REQUESTS")
    rate_limit_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS")

    # --- Derived helpers ----------------------------------------------------
    @property
    def data_path(self) -> Path:
        path = Path(self.app_data_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def prompts_path(self) -> Path:
        path = Path(self.prompts_dir)
        if not path.is_absolute():
            path = PROJECT_ROOT / path
        return path

    @property
    def ui_path(self) -> Path:
        return PROJECT_ROOT / "ui"

    @property
    def migrations_path(self) -> Path:
        return PROJECT_ROOT / "migrations"

    @property
    def cors_origins(self) -> list[str]:
        raw = self.cors_allow_origins.strip()
        if raw == "*" or not raw:
            return ["*"]
        return [o.strip() for o in raw.split(",") if o.strip()]

    @property
    def is_development(self) -> bool:
        return self.app_env == "development"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
