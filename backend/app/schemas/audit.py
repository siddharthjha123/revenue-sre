"""Immutable approval, audit, and recovery-outcome contracts."""

from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, computed_field, model_validator


class ApprovalDecisionType(StrEnum):
    APPROVED = "approved"
    REJECTED = "rejected"


class ApprovalDecision(BaseModel):
    """Immutable merchant decision tied to the exact approved plan hash."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    approval_id: UUID = Field(default_factory=uuid4)
    plan_id: UUID
    decision: ApprovalDecisionType
    decided_by: str = Field(min_length=1, max_length=256)
    decided_at: AwareDatetime
    plan_hash: str = Field(pattern=r"^[a-fA-F0-9]{64}$")


class AuditActorType(StrEnum):
    SYSTEM = "system"
    AGENT = "agent"
    MERCHANT = "merchant"
    RAZORPAY = "razorpay"


class AuditEventType(StrEnum):
    INCIDENT_CREATED = "incident_created"
    ANALYSIS_COMPLETED = "analysis_completed"
    PLAN_PROPOSED = "plan_proposed"
    PLAN_APPROVED = "plan_approved"
    PLAN_REJECTED = "plan_rejected"
    ACTION_EXECUTED = "action_executed"
    ACTION_FAILED = "action_failed"
    ACTION_SKIPPED = "action_skipped"
    OUTCOME_VERIFIED = "outcome_verified"


class AuditEvent(BaseModel):
    """Append-only record of a material decision or system action."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    audit_id: UUID = Field(default_factory=uuid4)
    merchant_id: UUID
    correlation_id: UUID
    incident_id: UUID | None = None
    plan_id: UUID | None = None
    event_type: AuditEventType
    actor_type: AuditActorType
    actor_id: str = Field(min_length=1, max_length=256)
    occurred_at: AwareDatetime
    details: dict[str, Any] = Field(default_factory=dict)


class FailureSegment(StrEnum):
    NETWORK_TIMEOUT = "network_timeout"
    AUTHENTICATION_FAILURE = "authentication_failure"
    INSUFFICIENT_FUNDS = "insufficient_funds"
    EXPIRED_INSTRUMENT = "expired_instrument"
    BANK_DECLINE = "bank_decline"
    RISK_DECLINE = "risk_decline"
    MERCHANT_INTEGRATION = "merchant_integration"
    GATEWAY_DEGRADATION = "gateway_degradation"
    UNKNOWN = "unknown"


class PlaybookOutcome(BaseModel):
    """Measured outcome for a versioned, merchant-approved playbook."""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    playbook_id: UUID
    merchant_id: UUID
    failure_segment: FailureSegment
    action: str = Field(min_length=1, max_length=256)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    approved: bool = False
    attempted_count: int = Field(ge=0)
    recovered_count: int = Field(ge=0)
    recovered_amount_subunits: int = Field(ge=0)
    executed_at: AwareDatetime

    @model_validator(mode="after")
    def recovered_cannot_exceed_attempted(self) -> "PlaybookOutcome":
        if self.recovered_count > self.attempted_count:
            raise ValueError("recovered_count cannot exceed attempted_count")
        return self

    @computed_field
    @property
    def success_rate(self) -> float:
        if self.attempted_count == 0:
            return 0.0
        return self.recovered_count / self.attempted_count
