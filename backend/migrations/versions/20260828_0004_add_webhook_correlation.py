"""Persist webhook correlation IDs across asynchronous processing.

Revision ID: 20260828_0004
Revises: 20260828_0003
Create Date: 2026-08-28
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260828_0004"
down_revision: str | None = "20260828_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add and deterministically backfill one correlation UUID per event."""

    op.add_column("webhook_events", sa.Column("correlation_id", sa.Uuid(), nullable=True))
    op.execute(
        """
        UPDATE webhook_events
        SET correlation_id = CAST(
            md5(CAST(id AS text) || '-webhook-correlation') AS uuid
        )
        """
    )
    op.alter_column("webhook_events", "correlation_id", nullable=False)
    op.create_index(
        "ix_webhook_events_correlation_id",
        "webhook_events",
        ["correlation_id"],
    )


def downgrade() -> None:
    """Remove durable correlation metadata."""

    op.drop_index("ix_webhook_events_correlation_id", table_name="webhook_events")
    op.drop_column("webhook_events", "correlation_id")
