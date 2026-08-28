"""Current normalized state of a Razorpay payment attempt."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    false,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from ...schemas.payment import ErrorSource, PaymentMethod, PaymentStatus
from ..base import Base


class PaymentAttempt(Base):
    """Latest payment state used by incident detection and recovery checks.

    Webhook history remains in ``webhook_events``. This table is a materialized
    current view and intentionally excludes customer email, phone and card data.
    """

    __tablename__ = "payment_attempts"
    __table_args__ = (
        UniqueConstraint(
            "merchant_id",
            "payment_id",
            name="uq_payment_attempts_merchant_payment",
        ),
        CheckConstraint("amount_subunits > 0", name="ck_payment_attempts_positive_amount"),
        CheckConstraint(
            "status IN ('created', 'authorized', 'captured', 'refunded', 'failed')",
            name="payment_status",
        ),
        CheckConstraint(
            "method IN ('card', 'upi', 'netbanking', 'wallet', 'emi', "
            "'cardless_emi', 'paylater', 'other')",
            name="payment_method",
        ),
        CheckConstraint(
            "error_source IS NULL OR error_source IN "
            "('customer', 'business', 'bank', 'gateway', 'internal', 'unknown')",
            name="payment_error_source",
        ),
        Index("ix_payment_attempts_merchant_status", "merchant_id", "status"),
        Index("ix_payment_attempts_merchant_created", "merchant_id", "provider_created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    razorpay_account_id: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    payment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    order_id: Mapped[str | None] = mapped_column(String(64))

    amount_subunits: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    method: Mapped[PaymentMethod] = mapped_column(
        Enum(
            PaymentMethod,
            name="payment_method",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        nullable=False,
    )
    captured: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    international: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())

    error_code: Mapped[str | None] = mapped_column(String(128))
    error_description: Mapped[str | None] = mapped_column(String(512))
    error_source: Mapped[ErrorSource | None] = mapped_column(
        Enum(
            ErrorSource,
            name="payment_error_source",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        )
    )
    error_step: Mapped[str | None] = mapped_column(String(128))
    error_reason: Mapped[str | None] = mapped_column(String(128))
    checkout_version: Mapped[str | None] = mapped_column(String(64))

    last_razorpay_event_id: Mapped[str] = mapped_column(String(255), nullable=False)
    provider_created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Razorpay payment entity creation timestamp.",
    )
    last_applied_event_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="Provider timestamp of the event that last changed current payment state.",
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
