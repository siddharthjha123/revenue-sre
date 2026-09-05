"""Persist verified Payment Link recovery outcomes.

Revision ID: 20260905_0010
Revises: 20260905_0009
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0010"
down_revision: str | None = "20260905_0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recovery_proposal_actions",
        sa.Column("recovered_payment_id", sa.String(length=64)),
    )
    op.add_column(
        "recovery_proposal_actions",
        sa.Column("recovered_amount_subunits", sa.Integer()),
    )
    op.add_column(
        "recovery_proposal_actions",
        sa.Column("recovered_at", sa.DateTime(timezone=True)),
    )


def downgrade() -> None:
    op.drop_column("recovery_proposal_actions", "recovered_at")
    op.drop_column("recovery_proposal_actions", "recovered_amount_subunits")
    op.drop_column("recovery_proposal_actions", "recovered_payment_id")
