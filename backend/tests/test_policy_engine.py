"""Deterministic, fail-closed policy tests."""

from datetime import UTC, datetime, timedelta

from backend.app.schemas.recovery import RecoveryPlanStatus
from backend.app.services.policy_engine import evaluate_recovery_plan


def test_execution_is_disabled_by_default(recovery_plan) -> None:
    decision = evaluate_recovery_plan(recovery_plan, execution_enabled=False)

    assert decision.allowed is False
    assert "recovery execution is disabled" in decision.reasons


def test_expired_plan_is_rejected_even_when_execution_enabled(recovery_plan) -> None:
    recovery_plan.expires_at = datetime.now(UTC) - timedelta(seconds=1)

    decision = evaluate_recovery_plan(recovery_plan, execution_enabled=True)

    assert decision.allowed is False
    assert "recovery plan has expired" in decision.reasons


def test_only_pending_plan_can_pass_enabled_day_one_policy(recovery_plan) -> None:
    recovery_plan.status = RecoveryPlanStatus.PENDING_APPROVAL

    decision = evaluate_recovery_plan(recovery_plan, execution_enabled=True)

    assert decision.allowed is True
    assert decision.reasons == ()
