"""Store explicit provider and last-applied event timestamps.

Revision ID: 20260828_0003
Revises: 20260827_0002
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0003"
down_revision: str | None = "20260827_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Expose provider ordering separately from receipt and payment creation."""

    op.add_column(
        "webhook_events",
        sa.Column(
            "provider_event_at",
            sa.DateTime(timezone=True),
            nullable=True,
            comment="Timestamp assigned by Razorpay to this event, not local receipt time.",
        ),
    )
    op.execute(
        """
        UPDATE webhook_events
        SET provider_event_at = COALESCE(
            to_timestamp(CAST(payload ->> 'created_at' AS double precision)),
            received_at
        )
        """
    )
    op.alter_column("webhook_events", "provider_event_at", nullable=False)
    op.create_index(
        "ix_webhook_events_merchant_provider_event",
        "webhook_events",
        ["merchant_id", "provider_event_at"],
    )
    op.alter_column(
        "payment_attempts",
        "last_event_at",
        new_column_name="last_applied_event_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
    op.alter_column(
        "payment_attempts",
        "provider_created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        comment="Razorpay payment entity creation timestamp.",
    )
    op.alter_column(
        "payment_attempts",
        "last_applied_event_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        comment="Provider timestamp of the event that last changed current payment state.",
    )


def downgrade() -> None:
    """Restore the previous timestamp name and JSON-only provider event time."""

    op.alter_column(
        "payment_attempts",
        "provider_created_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        comment=None,
    )
    op.alter_column(
        "payment_attempts",
        "last_applied_event_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
        comment=None,
    )
    op.alter_column(
        "payment_attempts",
        "last_applied_event_at",
        new_column_name="last_event_at",
        existing_type=sa.DateTime(timezone=True),
        existing_nullable=False,
    )
    op.drop_index("ix_webhook_events_merchant_provider_event", table_name="webhook_events")
    op.drop_column("webhook_events", "provider_event_at")
