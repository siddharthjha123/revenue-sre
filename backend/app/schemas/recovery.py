"""Recovery eligibility, action, and approval-plan contracts."""

from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from .payment import CurrencyCode, RazorpayPaymentId


class RecoveryActionType(StrEnum):
    NO_ACTION = "no_action"
    ALLOW_CUSTOMER_RETRY = "allow_customer_retry"
    CREATE_PAYMENT_LINK = "create_payment_link"
    SEND_PAYMENT_LINK = "send_payment_link"
    ENGINEERING_ESCALATION = "engineering_escalation"
    MANUAL_REVIEW = "manual_review"


class RecoveryPlanStatus(StrEnum):
    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RecoveryCandidate(BaseModel):
    """Safety evaluation for one failed payment."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    payment_id: RazorpayPaymentId
    eligible: bool
    eligibility_reason: str = Field(min_length=1, max_length=1000)
    already_paid: bool = False
    risk_excluded: bool = False
    contact_count: int = Field(default=0, ge=0)
    recommended_action: RecoveryActionType = RecoveryActionType.NO_ACTION

    @model_validator(mode="after")
    def enforce_exclusions(self) -> "RecoveryCandidate":
        """Excluded or already-paid attempts can never remain eligible."""

        if self.already_paid or self.risk_excluded:
            if self.eligible:
                raise ValueError("already-paid or risk-excluded payments cannot be eligible")
            if self.recommended_action not in {
                RecoveryActionType.NO_ACTION,
                RecoveryActionType.MANUAL_REVIEW,
            }:
                raise ValueError("excluded payments cannot receive an automated action")
        return self


class RecoveryAction(BaseModel):
    """One bounded action proposed as part of a recovery plan."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    action_id: UUID = Field(default_factory=uuid4)
    payment_id: RazorpayPaymentId
    action_type: RecoveryActionType
    amount_subunits: int = Field(gt=0)
    rationale: str = Field(min_length=1, max_length=1000)
    requires_approval: bool = True


class RecoveryPlan(BaseModel):
    """Merchant-reviewable collection of bounded recovery actions."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    plan_id: UUID = Field(default_factory=uuid4)
    merchant_id: UUID
    incident_id: UUID
    actions: list[RecoveryAction] = Field(min_length=1)
    total_amount_subunits: int = Field(gt=0)
    currency: CurrencyCode = "INR"
    maximum_customer_contacts: int = Field(default=1, ge=0, le=3)
    expires_at: AwareDatetime
    approval_required: bool = True
    status: RecoveryPlanStatus = RecoveryPlanStatus.DRAFT

    @model_validator(mode="after")
    def validate_financial_scope(self) -> "RecoveryPlan":
        action_total = sum(action.amount_subunits for action in self.actions)
        if action_total != self.total_amount_subunits:
            raise ValueError("total_amount_subunits must equal the sum of action amounts")
        if any(not action.requires_approval for action in self.actions):
            raise ValueError("all Day 1 recovery actions must require approval")
        if not self.approval_required:
            raise ValueError("recovery plans must require merchant approval")
        return self


class PolicyDecision(BaseModel):
    """Deterministic policy-engine result, independent of LLM reasoning."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    allowed: bool
    reasons: tuple[str, ...] = ()
