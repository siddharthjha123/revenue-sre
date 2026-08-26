"""Fail-closed policy checks for proposed recovery plans.

The language model may recommend actions, but it cannot authorize them. This
deterministic layer is the boundary between reasoning and financial execution.
"""

from datetime import UTC, datetime

from ..schemas.recovery import PolicyDecision, RecoveryPlan, RecoveryPlanStatus


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
