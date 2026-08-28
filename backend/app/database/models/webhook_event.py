"""Durable inbox model for authenticated Razorpay webhook events."""

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import JSON, CheckConstraint, DateTime, Enum, Index, String, Uuid, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


class WebhookStatus(StrEnum):
    """Lifecycle of an event in the durable processing inbox."""

    RECEIVED = "received"
    PROCESSED = "processed"
    IGNORED = "ignored"
    FAILED = "failed"


class WebhookEvent(Base):
    """Authenticated provider event stored once before asynchronous processing."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('received', 'processed', 'ignored', 'failed')",
            name="webhook_status",
        ),
        Index(
            "ix_webhook_events_merchant_provider_event",
            "merchant_id",
            "provider_event_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    merchant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)

    # Razorpay documents this header as unique per webhook event. The unique
    # constraint is the final protection against concurrent duplicate delivery.
    razorpay_event_id: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    razorpay_account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    provider_event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Timestamp assigned by Razorpay to this event, not local receipt time.",
    )

    payload_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"),
        nullable=False,
        comment="Sanitized JSON only; customer email and contact are excluded.",
    )

    status: Mapped[WebhookStatus] = mapped_column(
        Enum(
            WebhookStatus,
            name="webhook_status",
            native_enum=False,
            length=10,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        default=WebhookStatus.RECEIVED,
        server_default=WebhookStatus.RECEIVED.value,
        nullable=False,
    )
    failure_reason: Mapped[str | None] = mapped_column(String(2000))
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
