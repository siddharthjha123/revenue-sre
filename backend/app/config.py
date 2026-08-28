"""Typed application configuration loaded from environment variables.

Secrets must be supplied through the environment or a secret manager. They
must never be committed to source control.
"""

from functools import lru_cache
from typing import Literal
from uuid import UUID

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration with safe local defaults."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "Revenue SRE"
    environment: Literal["development", "test", "staging", "production"] = "development"
    log_level: str = "INFO"
    database_url: str = "sqlite+aiosqlite:///./revenue_sre.db"
    razorpay_key_id: str | None = None
    razorpay_key_secret: SecretStr | None = None
    razorpay_webhook_secret: SecretStr | None = None
    merchant_id: UUID | None = None
    razorpay_account_id: str | None = Field(
        default=None,
        pattern=r"^acc_[A-Za-z0-9]+$",
        max_length=64,
    )
    webhook_max_body_bytes: int = Field(default=262_144, ge=1024, le=10_485_760)
    worker_max_attempts: int = Field(default=5, ge=1, le=20)
    worker_lease_seconds: int = Field(default=60, ge=5, le=3600)
    worker_retry_base_seconds: int = Field(default=5, ge=1, le=3600)
    worker_retry_cap_seconds: int = Field(default=300, ge=1, le=86_400)
    # An empty environment value means "generate a process-unique ID".
    worker_id: str | None = Field(default=None, max_length=128)
    worker_poll_interval_seconds: float = Field(default=1.0, gt=0, le=60)
    worker_loop_error_backoff_seconds: float = Field(default=5.0, gt=0, le=300)
    worker_shutdown_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    worker_heartbeat_interval_seconds: float = Field(default=5.0, gt=0, le=60)
    worker_health_max_staleness_seconds: float = Field(default=20.0, gt=0, le=300)
    worker_heartbeat_path: str = Field(
        default=".runtime/revenue-sre-worker.heartbeat",
        min_length=1,
        max_length=1024,
    )
    worker_metrics_host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    worker_metrics_port: int = Field(default=9101, ge=0, le=65_535)
    execution_enabled: bool = False


@lru_cache
def get_settings() -> Settings:
    """Create settings once per process so all requests use one configuration."""

    return Settings()
