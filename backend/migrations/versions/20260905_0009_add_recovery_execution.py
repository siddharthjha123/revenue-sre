"""Persist restricted Razorpay Payment Link execution results.

Revision ID: 20260905_0009
Revises: 20260901_0008
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0009"
down_revision: str | None = "20260901_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "recovery_proposal_actions",
        sa.Column(
            "execution_status", sa.String(length=32), nullable=False, server_default="not_started"
        ),
    )
    op.add_column(
        "recovery_proposal_actions", sa.Column("provider_payment_link_id", sa.String(length=64))
    )
    op.add_column(
        "recovery_proposal_actions", sa.Column("payment_link_url", sa.String(length=2048))
    )
    op.add_column(
        "recovery_proposal_actions", sa.Column("execution_reference_id", sa.String(length=40))
    )
    op.add_column("recovery_proposal_actions", sa.Column("executed_at", sa.DateTime(timezone=True)))
    op.add_column(
        "recovery_proposal_actions", sa.Column("execution_error_code", sa.String(length=128))
    )
    op.create_unique_constraint(
        "uq_recovery_actions_execution_reference",
        "recovery_proposal_actions",
        ["execution_reference_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_recovery_actions_execution_reference",
        "recovery_proposal_actions",
        type_="unique",
    )
    for column in (
        "execution_error_code",
        "executed_at",
        "execution_reference_id",
        "payment_link_url",
        "provider_payment_link_id",
        "execution_status",
    ):
        op.drop_column("recovery_proposal_actions", column)
