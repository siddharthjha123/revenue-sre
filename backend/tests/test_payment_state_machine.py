"""Mandatory proofs for payment transition ordering and terminal states."""

from datetime import UTC, datetime, timedelta

from backend.app.schemas.payment import PaymentStatus
from backend.app.services.event_normalizer import (
    PaymentTransitionDecision,
    decide_payment_transition,
)

APPLIED_AT = datetime(2026, 8, 28, 10, 0, tzinfo=UTC)


def decide(
    current: PaymentStatus,
    incoming: PaymentStatus,
    *,
    incoming_at: datetime,
) -> PaymentTransitionDecision:
    return decide_payment_transition(
        current_status=current,
        last_applied_event_at=APPLIED_AT,
        incoming_status=incoming,
        incoming_event_at=incoming_at,
    )


def test_failed_followed_by_captured_is_a_forward_transition() -> None:
    result = decide(
        PaymentStatus.FAILED,
        PaymentStatus.CAPTURED,
        incoming_at=APPLIED_AT + timedelta(seconds=1),
    )

    assert result == PaymentTransitionDecision.APPLY


def test_older_captured_event_is_still_a_valid_forward_transition() -> None:
    result = decide(
        PaymentStatus.AUTHORIZED,
        PaymentStatus.CAPTURED,
        incoming_at=APPLIED_AT - timedelta(seconds=1),
    )

    assert result == PaymentTransitionDecision.APPLY


def test_older_failed_event_cannot_regress_captured_payment() -> None:
    result = decide(
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        incoming_at=APPLIED_AT - timedelta(minutes=5),
    )

    assert result == PaymentTransitionDecision.REGRESSION


def test_even_newer_failure_cannot_regress_captured_payment() -> None:
    result = decide(
        PaymentStatus.CAPTURED,
        PaymentStatus.FAILED,
        incoming_at=APPLIED_AT + timedelta(minutes=5),
    )

    assert result == PaymentTransitionDecision.REGRESSION


def test_older_same_state_event_is_stale() -> None:
    result = decide(
        PaymentStatus.FAILED,
        PaymentStatus.FAILED,
        incoming_at=APPLIED_AT - timedelta(seconds=1),
    )

    assert result == PaymentTransitionDecision.STALE
