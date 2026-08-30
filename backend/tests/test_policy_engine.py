"""Deterministic, fail-closed policy tests."""

from datetime import UTC, datetime, timedelta

from backend.app.schemas.recovery import RecoveryPlanStatus
from backend.app.services.policy_engine import (
    ProposalPolicyContext,
    evaluate_recovery_plan,
    evaluate_recovery_proposal,
)
from backend.tests.conftest import EVIDENCE_ID


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


def proposal_context(**overrides) -> ProposalPolicyContext:
    values = {
        "incident_status": "open",
        "incident_currency": "INR",
        "incident_money_at_risk_subunits": 200000,
        "eligible_payment_ids": frozenset({"pay_TU17WFeXwe0HQr"}),
        "eligible_evidence_ids": frozenset({str(EVIDENCE_ID)}),
        "maximum_plan_amount_subunits": 200000,
        "maximum_actions": 5,
        "maximum_plan_lifetime_minutes": 60,
        "maximum_customer_contacts": 1,
        "cooldown_active": False,
    }
    values.update(overrides)
    return ProposalPolicyContext(**values)


def test_proposal_policy_allows_bounded_unpaid_action(recovery_plan) -> None:
    recovery_plan.status = RecoveryPlanStatus.PENDING_APPROVAL

    decision = evaluate_recovery_proposal(recovery_plan, proposal_context())

    assert decision.allowed is True
    assert decision.reasons == ()


def test_proposal_policy_enforces_cooldown(recovery_plan) -> None:
    decision = evaluate_recovery_proposal(
        recovery_plan,
        proposal_context(cooldown_active=True),
    )

    assert decision.allowed is False
    assert "incident proposal cooldown is active" in decision.reasons


def test_proposal_policy_enforces_amount_and_contact_limits(recovery_plan) -> None:
    recovery_plan.maximum_customer_contacts = 2

    decision = evaluate_recovery_proposal(
        recovery_plan,
        proposal_context(
            maximum_plan_amount_subunits=50000,
            maximum_customer_contacts=1,
        ),
    )

    assert decision.allowed is False
    assert "proposal exceeds the configured amount limit" in decision.reasons
    assert "proposal exceeds the customer contact limit" in decision.reasons