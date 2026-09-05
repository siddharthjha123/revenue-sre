"""Align recovery execution status with the ORM enum contract.

Revision ID: 20260905_0011
Revises: 20260905_0010
Create Date: 2026-09-05
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "20260905_0011"
down_revision: str | None = "20260905_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

execution_status_enum = sa.Enum(
    "not_started",
    "succeeded",
    "failed",
    "skipped",
    name="recovery_execution_status",
    native_enum=False,
)


def upgrade() -> None:
    """Use the same constrained string type declared by the ORM model."""

    op.alter_column(
        "recovery_proposal_actions",
        "execution_status",
        existing_type=sa.String(length=32),
        type_=execution_status_enum,
        existing_nullable=False,
        existing_server_default="not_started",
    )


def downgrade() -> None:
    """Restore the original generic string representation."""

    op.alter_column(
        "recovery_proposal_actions",
        "execution_status",
        existing_type=execution_status_enum,
        type_=sa.String(length=32),
        existing_nullable=False,
        existing_server_default="not_started",
    )
