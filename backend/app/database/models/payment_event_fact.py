"""Immutable normalized facts derived from authenticated payment webhooks."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, Enum, ForeignKey, Index, Integer, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from ...schemas.payment import ErrorSource, PaymentMethod, PaymentStatus
from ..base import Base


class PaymentEventFact(Base):
    """One append-only, PII-minimized fact for one provider event.

    ``payment_attempts`` answers "what is the payment state now?" while this
    table answers "what exactly happened?". The unique webhook foreign key
    makes processing retries idempotent.
    """

    __tablename__ = "payment_event_facts"
    __table_args__ = (
        Index(
            "ix_payment_event_facts_detection_window",
            "merchant_id",
            "provider_event_at",
            "method",
            "currency",
        ),
        Index(
            "ix_payment_event_facts_failure_segment",
            "merchant_id",
            "status",
            "bank",
            "error_reason",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    webhook_event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("webhook_events.id", ondelete="RESTRICT"),
        unique=True,
        nullable=False,
    )
    merchant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    razorpay_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    razorpay_account_id: Mapped[str] = mapped_column(String(64), nullable=False)
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(64))
    amount_subunits: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_fact_status",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(
            PaymentMethod,
            name="payment_fact_method",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    bank: Mapped[str | None] = mapped_column(String(64))
    wallet: Mapped[str | None] = mapped_column(String(64))
    error_code: Mapped[str | None] = mapped_column(String(128))
    error_source: Mapped[ErrorSource | None] = mapped_column(
        Enum(
            ErrorSource,
            name="payment_fact_error_source",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        )
    )
    error_step: Mapped[str | None] = mapped_column(String(128))
    error_reason: Mapped[str | None] = mapped_column(String(128))
    payment_created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    provider_event_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
