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
    incident_current_window_minutes: int = Field(default=5, ge=1, le=1440)
    incident_baseline_window_minutes: int = Field(default=30, ge=5, le=10080)
    incident_minimum_attempts: int = Field(default=20, ge=1, le=100000)
    incident_minimum_failures: int = Field(default=5, ge=1, le=100000)
    incident_minimum_failure_rate: float = Field(default=0.20, ge=0, le=1)
    incident_minimum_rate_increase: float = Field(default=0.10, ge=0, le=1)
    incident_baseline_multiplier: float = Field(default=2.0, ge=1, le=100)
    recovery_policy_version: str = Field(default="recovery-policy-v1", max_length=32)
    recovery_max_plan_amount_subunits: int = Field(default=1_000_000, ge=1)
    recovery_max_actions_per_plan: int = Field(default=20, ge=1, le=100)
    recovery_max_plan_lifetime_minutes: int = Field(default=60, ge=1, le=1440)
    recovery_proposal_cooldown_minutes: int = Field(default=15, ge=0, le=10080)
    recovery_max_customer_contacts: int = Field(default=1, ge=0, le=3)
    execution_enabled: bool = False
    mcp_host: str = Field(default="127.0.0.1", min_length=1, max_length=255)
    mcp_port: int = Field(default=8010, ge=1, le=65_535)
    mcp_allowed_hosts: list[str] = Field(
        default_factory=lambda: [
            "127.0.0.1",
            "127.0.0.1:*",
            "localhost",
            "localhost:*",
        ]
    )
    mcp_allowed_origins: list[str] = Field(default_factory=list)


@lru_cache
def get_settings() -> Settings:
    """Create settings once per process so all requests use one configuration."""

    return Settings()
