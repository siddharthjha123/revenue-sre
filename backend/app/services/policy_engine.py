"""Fail-closed policy checks for proposed recovery plans.

The language model may recommend actions, but it cannot authorize them. This
deterministic layer is the boundary between reasoning and financial execution.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from ..schemas.incident import IncidentStatus
from ..schemas.recovery import (
    PolicyDecision,
    RecoveryActionType,
    RecoveryPlan,
    RecoveryPlanStatus,
)

POLICY_VERSION = "recovery-policy-v1"


@dataclass(frozen=True, slots=True)
class ProposalPolicyContext:
    """Trusted facts used to validate an untrusted proposal."""

    incident_status: IncidentStatus
    incident_currency: str
    incident_money_at_risk_subunits: int
    eligible_payment_amounts: Mapping[str, int]
    eligible_evidence_ids: frozenset[str]
    maximum_plan_amount_subunits: int
    maximum_actions: int
    maximum_plan_lifetime_minutes: int
    maximum_customer_contacts: int
    cooldown_active: bool = False


def evaluate_recovery_proposal(
    plan: RecoveryPlan,
    context: ProposalPolicyContext,
    *,
    now: datetime | None = None,
    policy_version: str = POLICY_VERSION,
) -> PolicyDecision:
    """Fail closed when any bounded proposal rule is violated."""

    checked_at = now or datetime.now(UTC)
    reasons: list[str] = []
    if context.incident_status not in {
        IncidentStatus.OPEN,
        IncidentStatus.INVESTIGATING,
    }:
        reasons.append("incident is not actionable")
    if plan.currency != context.incident_currency:
        reasons.append("proposal currency does not match incident currency")
    if plan.total_amount_subunits > context.maximum_plan_amount_subunits:
        reasons.append("proposal exceeds the configured amount limit")
    if plan.total_amount_subunits > context.incident_money_at_risk_subunits:
        reasons.append("proposal exceeds incident money at risk")
    if len(plan.actions) > context.maximum_actions:
        reasons.append("proposal exceeds the action rate limit")
    if plan.maximum_customer_contacts > context.maximum_customer_contacts:
        reasons.append("proposal exceeds the customer contact limit")
    if plan.expires_at <= checked_at:
        reasons.append("recovery plan has expired")
    if plan.expires_at > checked_at + timedelta(minutes=context.maximum_plan_lifetime_minutes):
        reasons.append("recovery plan lifetime exceeds the configured limit")
    if context.cooldown_active:
        reasons.append("incident proposal cooldown is active")
    if any(action.payment_id not in context.eligible_payment_amounts for action in plan.actions):
        reasons.append("proposal includes an ineligible or already-paid payment")
    if any(
        action.payment_id in context.eligible_payment_amounts
        and action.amount_subunits != context.eligible_payment_amounts[action.payment_id]
        for action in plan.actions
    ):
        reasons.append("proposal amount does not match the current payment amount")
    if not plan.evidence_ids:
        reasons.append("proposal must reference incident evidence")
    elif any(str(item) not in context.eligible_evidence_ids for item in plan.evidence_ids):
        reasons.append("proposal references evidence outside the incident")
    if any(not action.requires_approval for action in plan.actions):
        reasons.append("every recovery action requires merchant approval")
    allowed_actions = {
        RecoveryActionType.ALLOW_CUSTOMER_RETRY,
        RecoveryActionType.CREATE_PAYMENT_LINK,
        RecoveryActionType.ENGINEERING_ESCALATION,
        RecoveryActionType.MANUAL_REVIEW,
    }
    if any(action.action_type not in allowed_actions for action in plan.actions):
        reasons.append("proposal contains an action unavailable in this release")
    payment_ids = [action.payment_id for action in plan.actions]
    if len(set(payment_ids)) != len(payment_ids):
        reasons.append("proposal contains more than one action for a payment")
    if plan.status not in {
        RecoveryPlanStatus.DRAFT,
        RecoveryPlanStatus.PENDING_APPROVAL,
    }:
        reasons.append("proposal is not awaiting merchant review")
    if not plan.approval_required:
        reasons.append("merchant approval is mandatory")
    return PolicyDecision(
        allowed=not reasons,
        reasons=tuple(reasons),
        policy_version=policy_version,
    )


def evaluate_recovery_plan(plan: RecoveryPlan, *, execution_enabled: bool) -> PolicyDecision:
    """Return whether a plan may proceed to merchant approval.

    Day 1 deliberately denies execution. Later policies will additionally
    validate an immutable approval, amount limits, idempotency, payment status,
    contact limits, and plan expiration immediately before each money action.
    """

    reasons: list[str] = []
    now = datetime.now(UTC)

    if not execution_enabled:
        reasons.append("recovery execution is disabled")
    if plan.expires_at <= now:
        reasons.append("recovery plan has expired")
    if plan.status not in {RecoveryPlanStatus.DRAFT, RecoveryPlanStatus.PENDING_APPROVAL}:
        reasons.append("plan is not awaiting merchant review")
    if not plan.approval_required:
        reasons.append("merchant approval is mandatory")

    return PolicyDecision(allowed=not reasons, reasons=tuple(reasons))
