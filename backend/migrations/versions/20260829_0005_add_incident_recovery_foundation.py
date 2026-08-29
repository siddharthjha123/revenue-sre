"""Add normalized facts, incidents, evidence, proposals, approvals, and audit.

Revision ID: 20260829_0005
Revises: 20260828_0004
Create Date: 2026-08-29
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260829_0005"
down_revision: str | None = "20260828_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the durable incident and approval foundation."""

    op.add_column("payment_attempts", sa.Column("bank", sa.String(length=64)))
    op.add_column("payment_attempts", sa.Column("wallet", sa.String(length=64)))

    op.create_table(
        "payment_event_facts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("webhook_event_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("razorpay_event_id", sa.String(length=255), nullable=False),
        sa.Column("razorpay_account_id", sa.String(length=64), nullable=False),
        sa.Column("event_type", sa.String(length=128), nullable=False),
        sa.Column("payment_id", sa.String(length=64), nullable=False),
        sa.Column("order_id", sa.String(length=64)),
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
                name="payment_fact_status",
                native_enum=False,
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
                name="payment_fact_method",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("bank", sa.String(length=64)),
        sa.Column("wallet", sa.String(length=64)),
        sa.Column("error_code", sa.String(length=128)),
        sa.Column(
            "error_source",
            sa.Enum(
                "customer",
                "business",
                "bank",
                "gateway",
                "internal",
                "unknown",
                name="payment_fact_error_source",
                native_enum=False,
            ),
        ),
        sa.Column("error_step", sa.String(length=128)),
        sa.Column("error_reason", sa.String(length=128)),
        sa.Column("payment_created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("provider_event_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["webhook_event_id"], ["webhook_events.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("webhook_event_id"),
    )
    op.create_index("ix_payment_event_facts_merchant_id", "payment_event_facts", ["merchant_id"])
    op.create_index(
        "ix_payment_event_facts_detection_window",
        "payment_event_facts",
        ["merchant_id", "provider_event_at", "method", "currency"],
    )
    op.create_index(
        "ix_payment_event_facts_failure_segment",
        "payment_event_facts",
        ["merchant_id", "status", "bank", "error_reason"],
    )

    op.create_table(
        "incidents",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "incident_type",
            sa.Enum(
                "single_payment_failure",
                "payment_failure_spike",
                "payment_method_degradation",
                "bank_decline_spike",
                "merchant_integration_regression",
                "gateway_degradation",
                "unknown",
                name="incident_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "open",
                "investigating",
                "mitigated",
                "resolved",
                "false_positive",
                name="incident_status",
                native_enum=False,
            ),
            server_default="open",
            nullable=False,
        ),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("method", sa.String(length=32), nullable=False),
        sa.Column("bank", sa.String(length=64)),
        sa.Column("error_reason", sa.String(length=128), nullable=False),
        sa.Column("detector_version", sa.String(length=32), nullable=False),
        sa.Column("baseline_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_window_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("current_window_end", sa.DateTime(timezone=True), nullable=False),
        sa.Column("baseline_attempt_count", sa.Integer(), nullable=False),
        sa.Column("baseline_failure_count", sa.Integer(), nullable=False),
        sa.Column("current_attempt_count", sa.Integer(), nullable=False),
        sa.Column("current_failure_count", sa.Integer(), nullable=False),
        sa.Column("baseline_failure_rate", sa.Float(), nullable=False),
        sa.Column("current_failure_rate", sa.Float(), nullable=False),
        sa.Column("revenue_at_risk_subunits", sa.Integer(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("opened_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_detected_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("merchant_id", "fingerprint", name="uq_incidents_merchant_fingerprint"),
    )
    op.create_index("ix_incidents_merchant_id", "incidents", ["merchant_id"])
    op.create_index(
        "ix_incidents_merchant_status_updated", "incidents", ["merchant_id", "status", "updated_at"]
    )

    op.create_table(
        "incident_evidence",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("payment_event_fact_id", sa.Uuid()),
        sa.Column("evidence_key", sa.String(length=128), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "razorpay_fact",
                "merchant_fact",
                "sandbox_metric",
                "agent_hypothesis",
                name="evidence_kind",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("summary", sa.String(length=1000), nullable=False),
        sa.Column("source_reference", sa.String(length=256)),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(
            ["payment_event_fact_id"], ["payment_event_facts.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("incident_id", "evidence_key", name="uq_incident_evidence_key"),
    )
    op.create_index(
        "ix_incident_evidence_merchant_incident",
        "incident_evidence",
        ["merchant_id", "incident_id"],
    )

    op.create_table(
        "recovery_proposals",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "draft",
                "pending_approval",
                "approved",
                "rejected",
                "executing",
                "completed",
                "failed",
                "cancelled",
                name="recovery_plan_status",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("total_amount_subunits", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("maximum_customer_contacts", sa.Integer(), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
        sa.Column("policy_version", sa.String(length=32), nullable=False),
        sa.Column("policy_allowed", sa.Boolean(), nullable=False),
        sa.Column("policy_reasons", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("created_by", sa.String(length=256), nullable=False),
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
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recovery_proposals_merchant_id", "recovery_proposals", ["merchant_id"])
    op.create_index(
        "ix_recovery_proposals_merchant_status", "recovery_proposals", ["merchant_id", "status"]
    )
    op.create_index(
        "ix_recovery_proposals_incident_created",
        "recovery_proposals",
        ["incident_id", "created_at"],
    )

    op.create_table(
        "recovery_proposal_actions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.String(length=64), nullable=False),
        sa.Column(
            "action_type",
            sa.Enum(
                "no_action",
                "allow_customer_retry",
                "create_payment_link",
                "send_payment_link",
                "engineering_escalation",
                "manual_review",
                name="recovery_action_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("amount_subunits", sa.Integer(), nullable=False),
        sa.Column("rationale", sa.String(length=1000), nullable=False),
        sa.Column("requires_approval", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(["proposal_id"], ["recovery_proposals.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_recovery_actions_proposal", "recovery_proposal_actions", ["proposal_id"])

    op.create_table(
        "approval_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("proposal_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid(), nullable=False),
        sa.Column(
            "decision",
            sa.Enum("approved", "rejected", name="approval_decision_type", native_enum=False),
            nullable=False,
        ),
        sa.Column("decided_by", sa.String(length=256), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("plan_hash", sa.String(length=64), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposal_id"], ["recovery_proposals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("proposal_id", name="uq_approval_records_proposal"),
    )
    op.create_index(
        "ix_approval_records_merchant_decided", "approval_records", ["merchant_id", "decided_at"]
    )

    op.create_table(
        "audit_records",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("merchant_id", sa.Uuid(), nullable=False),
        sa.Column("correlation_id", sa.Uuid(), nullable=False),
        sa.Column("incident_id", sa.Uuid()),
        sa.Column("proposal_id", sa.Uuid()),
        sa.Column(
            "event_type",
            sa.Enum(
                "incident_created",
                "analysis_completed",
                "plan_proposed",
                "policy_validated",
                "approval_requested",
                "plan_approved",
                "plan_rejected",
                "action_executed",
                "action_failed",
                "action_skipped",
                "outcome_verified",
                name="audit_event_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "actor_type",
            sa.Enum(
                "system",
                "agent",
                "merchant",
                "razorpay",
                name="audit_actor_type",
                native_enum=False,
            ),
            nullable=False,
        ),
        sa.Column("actor_id", sa.String(length=256), nullable=False),
        sa.Column("details", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["incident_id"], ["incidents.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["proposal_id"], ["recovery_proposals.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_audit_records_merchant_occurred", "audit_records", ["merchant_id", "occurred_at"]
    )


def downgrade() -> None:
    """Remove the incident and approval foundation in dependency order."""

    op.drop_index("ix_audit_records_merchant_occurred", table_name="audit_records")
    op.drop_table("audit_records")
    op.drop_index("ix_approval_records_merchant_decided", table_name="approval_records")
    op.drop_table("approval_records")
    op.drop_index("ix_recovery_actions_proposal", table_name="recovery_proposal_actions")
    op.drop_table("recovery_proposal_actions")
    op.drop_index("ix_recovery_proposals_incident_created", table_name="recovery_proposals")
    op.drop_index("ix_recovery_proposals_merchant_status", table_name="recovery_proposals")
    op.drop_index("ix_recovery_proposals_merchant_id", table_name="recovery_proposals")
    op.drop_table("recovery_proposals")
    op.drop_index("ix_incident_evidence_merchant_incident", table_name="incident_evidence")
    op.drop_table("incident_evidence")
    op.drop_index("ix_incidents_merchant_status_updated", table_name="incidents")
    op.drop_index("ix_incidents_merchant_id", table_name="incidents")
    op.drop_table("incidents")
    op.drop_index("ix_payment_event_facts_failure_segment", table_name="payment_event_facts")
    op.drop_index("ix_payment_event_facts_detection_window", table_name="payment_event_facts")
    op.drop_index("ix_payment_event_facts_merchant_id", table_name="payment_event_facts")
    op.drop_table("payment_event_facts")
    op.drop_column("payment_attempts", "wallet")
    op.drop_column("payment_attempts", "bank")
