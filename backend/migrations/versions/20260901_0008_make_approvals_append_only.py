"""Make merchant approval records append-only and retain decision reasons.

Revision ID: 20260901_0008
Revises: 20260830_0007
Create Date: 2026-09-01
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260901_0008"
down_revision: str | None = "20260830_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Store an optional reason and reject PostgreSQL update/delete attempts."""

    op.add_column(
        "recovery_proposals",
        sa.Column(
            "eligible_payment_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.add_column(
        "recovery_proposals",
        sa.Column(
            "omitted_payment_count",
            sa.Integer(),
            nullable=False,
            server_default="0",
        ),
    )
    op.execute(
        """
        UPDATE recovery_proposals
        SET eligible_payment_count = (
            SELECT COUNT(DISTINCT payment_id)
            FROM recovery_proposal_actions
            WHERE recovery_proposal_actions.proposal_id = recovery_proposals.id
        )
        """
    )
    op.alter_column("recovery_proposals", "eligible_payment_count", server_default=None)
    op.alter_column("recovery_proposals", "omitted_payment_count", server_default=None)

    op.add_column(
        "approval_records",
        sa.Column("reason", sa.String(length=1000), nullable=True),
    )

    if op.get_bind().dialect.name == "postgresql":
        op.execute(
            """
            CREATE FUNCTION prevent_approval_record_mutation()
            RETURNS trigger
            LANGUAGE plpgsql
            AS $$
            BEGIN
                RAISE EXCEPTION 'approval_records are append-only';
            END;
            $$
            """
        )
        op.execute(
            """
            CREATE TRIGGER approval_records_append_only
            BEFORE UPDATE OR DELETE ON approval_records
            FOR EACH ROW
            EXECUTE FUNCTION prevent_approval_record_mutation()
            """
        )


def downgrade() -> None:
    """Remove the append-only database guard and optional decision reason."""

    if op.get_bind().dialect.name == "postgresql":
        op.execute("DROP TRIGGER IF EXISTS approval_records_append_only ON approval_records")
        op.execute("DROP FUNCTION IF EXISTS prevent_approval_record_mutation()")

    op.drop_column("approval_records", "reason")
    op.drop_column("recovery_proposals", "omitted_payment_count")
    op.drop_column("recovery_proposals", "eligible_payment_count")
