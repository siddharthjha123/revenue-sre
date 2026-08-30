"""Separate the webhook inbox from the durable processing queue.

Revision ID: 20260827_0002
Revises: 20260826_0001
Create Date: 2026-08-27
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260827_0002"
down_revision: str | None = "20260826_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the durable job queue and remove queue state from inbox rows."""

    op.create_table(
        "processing_jobs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("webhook_event_id", sa.Uuid(), nullable=False),
        sa.Column(
            "job_type",
            sa.String(length=64),
            server_default="process_webhook",
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "pending",
                "processing",
                "retry_scheduled",
                "succeeded",
                "dead_letter",
                name="processing_job_status",
                native_enum=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("priority", sa.Integer(), server_default="100", nullable=False),
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default="5", nullable=False),
        sa.Column(
            "available_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("locked_by", sa.String(length=128), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.String(length=1000), nullable=True),
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
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_processing_jobs_attempt_count"),
        sa.CheckConstraint("max_attempts > 0", name="ck_processing_jobs_max_attempts"),
        sa.CheckConstraint("priority >= 0", name="ck_processing_jobs_priority"),
        sa.CheckConstraint(
            "status IN ('pending', 'processing', 'retry_scheduled', 'succeeded', 'dead_letter')",
            name="processing_job_status",
        ),
        sa.ForeignKeyConstraint(
            ["webhook_event_id"],
            ["webhook_events.id"],
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("webhook_event_id"),
    )
    op.create_index(
        "ix_processing_jobs_claim",
        "processing_jobs",
        ["status", "available_at", "priority", "created_at"],
    )
    op.create_index("ix_processing_jobs_merchant_id", "processing_jobs", ["merchant_id"])

    # Preserve already accepted events from the previous schema. The derived
    # UUID is deterministic, so a retried migration cannot invent duplicates.
    op.execute(
        sa.text(
            """
            INSERT INTO processing_jobs (id, merchant_id, webhook_event_id)
            SELECT CAST(md5(CAST(id AS text) || '-processing-job') AS uuid), merchant_id, id
            FROM webhook_events
            WHERE status = 'received'
              AND event_type IN ('payment.failed', 'payment.authorized', 'payment.captured')
            ON CONFLICT (webhook_event_id) DO NOTHING
            """
        )
    )

    op.drop_index("ix_webhook_events_status_next_attempt", table_name="webhook_events")
    op.drop_constraint("webhook_status", "webhook_events", type_="check")
    op.execute("UPDATE webhook_events SET status = 'received' WHERE status = 'processing'")
    op.create_check_constraint(
        "webhook_status",
        "webhook_events",
        "status IN ('received', 'processed', 'ignored', 'failed')",
    )
    op.drop_constraint("ck_webhook_events_attempt_count", "webhook_events", type_="check")
    op.drop_column("webhook_events", "next_attempt_at")
    op.drop_column("webhook_events", "lease_expires_at")
    op.drop_column("webhook_events", "locked_by")
    op.drop_column("webhook_events", "attempt_count")


def downgrade() -> None:
    """Restore the original combined inbox/queue schema."""

    op.add_column(
        "webhook_events",
        sa.Column("attempt_count", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("webhook_events", sa.Column("locked_by", sa.String(length=128)))
    op.add_column("webhook_events", sa.Column("lease_expires_at", sa.DateTime(timezone=True)))
    op.add_column("webhook_events", sa.Column("next_attempt_at", sa.DateTime(timezone=True)))
    op.create_check_constraint(
        "ck_webhook_events_attempt_count", "webhook_events", "attempt_count >= 0"
    )
    op.drop_constraint("webhook_status", "webhook_events", type_="check")
    op.create_check_constraint(
        "webhook_status",
        "webhook_events",
        "status IN ('received', 'processing', 'processed', 'ignored', 'failed')",
    )
    op.create_index(
        "ix_webhook_events_status_next_attempt",
        "webhook_events",
        ["status", "next_attempt_at"],
    )

    op.drop_index("ix_processing_jobs_merchant_id", table_name="processing_jobs")
    op.drop_index("ix_processing_jobs_claim", table_name="processing_jobs")
    op.drop_table("processing_jobs")
