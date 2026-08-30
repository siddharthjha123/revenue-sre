"""Bind recovery proposals to the evidence reviewed by the agent.

Revision ID: 20260830_0007
Revises: 20260829_0006
Create Date: 2026-08-30
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260830_0007"
down_revision: str | None = "20260829_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add a non-null JSON evidence identity list with a safe empty backfill."""

    op.add_column(
        "recovery_proposals",
        sa.Column(
            "evidence_ids",
            sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), "postgresql"),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.alter_column("recovery_proposals", "evidence_ids", server_default=None)


def downgrade() -> None:
    """Remove proposal-to-evidence bindings."""

    op.drop_column("recovery_proposals", "evidence_ids")
