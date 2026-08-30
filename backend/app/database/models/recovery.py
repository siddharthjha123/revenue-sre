"""Persistent recovery proposals, immutable decisions, and audit entries."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...schemas.audit import ApprovalDecisionType, AuditActorType, AuditEventType
from ...schemas.recovery import RecoveryActionType, RecoveryPlanStatus
from ..base import Base


class RecoveryProposal(Base):
    """Exact, hash-bound proposal awaiting an explicit merchant decision."""

    __tablename__ = "recovery_proposals"
    __table_args__ = (
        Index("ix_recovery_proposals_merchant_status", "merchant_id", "status"),
        Index("ix_recovery_proposals_incident_created", "incident_id", "created_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[RecoveryPlanStatus] = mapped_column(
        Enum(
            RecoveryPlanStatus,
            name="recovery_plan_status",
            native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
    )
    total_amount_subunits: Mapped[int] = mapped_column(Integer, nullable=False)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    maximum_customer_contacts: Mapped[int] = mapped_column(Integer, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    policy_version: Mapped[str] = mapped_column(String(32), nullable=False)
    policy_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    policy_reasons: Mapped[list[str]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=list
    )
    created_by: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class RecoveryProposalAction(Base):
    """One bounded action in a proposal; actions are not executable in Block 2."""

    __tablename__ = "recovery_proposal_actions"
    __table_args__ = (Index("ix_recovery_actions_proposal", "proposal_id"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    proposal_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recovery_proposals.id", ondelete="CASCADE"), nullable=False
    )
    payment_id: Mapped[str] = mapped_column(String(64), nullable=False)
    action_type: Mapped[RecoveryActionType] = mapped_column(
        Enum(
            RecoveryActionType,
            name="recovery_action_type",
            native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
    )
    amount_subunits: Mapped[int] = mapped_column(Integer, nullable=False)
    rationale: Mapped[str] = mapped_column(String(1000), nullable=False)
    requires_approval: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ApprovalRecord(Base):
    """Immutable decision bound to the proposal hash reviewed by the merchant."""

    __tablename__ = "approval_records"
    __table_args__ = (
        UniqueConstraint("proposal_id", name="uq_approval_records_proposal"),
        Index("ix_approval_records_merchant_decided", "merchant_id", "decided_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    proposal_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recovery_proposals.id", ondelete="RESTRICT"), nullable=False
    )
    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="RESTRICT"), nullable=False
    )
    decision: Mapped[ApprovalDecisionType] = mapped_column(
        Enum(
            ApprovalDecisionType,
            name="approval_decision_type",
            native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
    )
    decided_by: Mapped[str] = mapped_column(String(256), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    plan_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class AuditRecord(Base):
    """Append-only material action and decision record."""

    __tablename__ = "audit_records"
    __table_args__ = (Index("ix_audit_records_merchant_occurred", "merchant_id", "occurred_at"),)

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    correlation_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    incident_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="RESTRICT")
    )
    proposal_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("recovery_proposals.id", ondelete="RESTRICT")
    )
    event_type: Mapped[AuditEventType] = mapped_column(
        Enum(
            AuditEventType,
            name="audit_event_type",
            native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
    )
    actor_type: Mapped[AuditActorType] = mapped_column(
        Enum(
            AuditActorType,
            name="audit_actor_type",
            native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
    )
    actor_id: Mapped[str] = mapped_column(String(256), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
