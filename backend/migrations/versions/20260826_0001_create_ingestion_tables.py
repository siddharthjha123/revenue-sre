"""Create the webhook inbox and normalized payment tables.

Revision ID: 20260826_0001
Revises:
Create Date: 2026-08-26
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260826_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create durable ingestion tables and their query indexes."""

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("razorpay_event_id", sa.String(length=255), nullable=False),
        sa.Column("razorpay_account_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payload_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            comment="Sanitized JSON only; customer email and contact are excluded.",
        ),
        sa.Column(
            "status",
            sa.Enum(
                "received",
                "processing",
                "processed",
                "ignored",
                "failed",
                name="webhook_status",
                native_enum=False,
                create_constraint=True,
            ),
            server_default="received",
            nullable=False,
        ),
        sa.Column("failure_reason", sa.String(length=2000), nullable=True),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_webhook_events_attempt_count"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("razorpay_event_id"),
    )
    op.create_index("ix_webhook_events_event_type", "webhook_events", ["event_type"])
    op.create_index("ix_webhook_events_merchant_id", "webhook_events", ["merchant_id"])
    op.create_index(
        "ix_webhook_events_razorpay_account_id",
        "webhook_events",
        ["razorpay_account_id"],
    )
    op.create_index(
        "ix_webhook_events_status_next_attempt",
        "webhook_events",
        ["status", "next_attempt_at"],
    )

    op.create_table(
        "payment_attempts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("razorpay_account_id", sa.String(length=64), nullable=False),
        sa.Column("payment_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64), nullable=True),
        sa.Column("amount_subunits", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "created",
                "authorized",
                "captured",
                "refunded",
                "failed",
                name="payment_status",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column(
            "method",
            sa.Enum(
                "card",
                "upi",
                "netbanking",
                "wallet",
                "emi",
                "cardless_emi",
                "paylater",
                "other",
                name="payment_method",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=False,
        ),
        sa.Column("captured", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "international",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=128), nullable=True),
        sa.Column("error_description", sa.String(length=512), nullable=True),
        sa.Column(
            "error_source",
            sa.Enum(
                "customer",
                "business",
                "bank",
                "gateway",
                "internal",
                "unknown",
                name="payment_error_source",
                native_enum=False,
                create_constraint=True,
            ),
            nullable=True,
        ),
        sa.Column("error_step", sa.String(length=128), nullable=True),
        sa.Column("error_reason", sa.String(length=128), nullable=True),
        sa.Column("checkout_version", sa.String(length=64), nullable=True),
        sa.Column("last_razorpay_event_id", sa.String(length=255), nullable=False),
        sa.Column("provider_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount_subunits > 0",
            name="ck_payment_attempts_positive_amount",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "merchant_id",
            "payment_id",
            name="uq_payment_attempts_merchant_payment",
        ),
    )
    op.create_index(
        "ix_payment_attempts_merchant_created",
        "payment_attempts",
        ["merchant_id", "provider_created_at"],
    )
    op.create_index("ix_payment_attempts_merchant_id", "payment_attempts", ["merchant_id"])
    op.create_index(
        "ix_payment_attempts_merchant_status",
        "payment_attempts",
        ["merchant_id", "status"],
    )
    op.create_index(
        "ix_payment_attempts_razorpay_account_id",
        "payment_attempts",
        ["razorpay_account_id"],
    )


def downgrade() -> None:
    """Remove ingestion tables in reverse dependency order."""

    op.drop_index("ix_payment_attempts_razorpay_account_id", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_merchant_status", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_merchant_id", table_name="payment_attempts")
    op.drop_index("ix_payment_attempts_merchant_created", table_name="payment_attempts")
    op.drop_table("payment_attempts")

    op.drop_index("ix_webhook_events_status_next_attempt", table_name="webhook_events")
    op.drop_index("ix_webhook_events_razorpay_account_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_merchant_id", table_name="webhook_events")
    op.drop_index("ix_webhook_events_event_type", table_name="webhook_events")
    op.drop_table("webhook_events")
