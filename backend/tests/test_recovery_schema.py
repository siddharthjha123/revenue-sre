"""Recovery safety-invariant tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.app.schemas.recovery import RecoveryAction, RecoveryCandidate, RecoveryPlan


def test_already_paid_payment_cannot_be_eligible() -> None:
    with pytest.raises(ValidationError, match="already-paid"):
        RecoveryCandidate(
            payment_id="pay_TU17WFeXwe0HQr",
            eligible=True,
            eligibility_reason="incorrectly eligible",
            already_paid=True,
            recommended_action="create_payment_link",
        )


def test_plan_total_must_equal_action_total() -> None:
    action = RecoveryAction(
        payment_id="pay_TU17WFeXwe0HQr",
        action_type="create_payment_link",
        amount_subunits=100000,
        rationale="One retry path.",
    )

    with pytest.raises(ValidationError, match="sum of action amounts"):
        RecoveryPlan(
            merchant_id=UUID("c56a4180-65aa-42ec-a945-5fd21dec0538"),
            incident_id=UUID("16fd2706-8baf-433b-82eb-8c7fada847da"),
            actions=[action],
            total_amount_subunits=1,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


def test_day_one_plan_cannot_bypass_approval() -> None:
    action = RecoveryAction(
        payment_id="pay_TU17WFeXwe0HQr",
        action_type="create_payment_link",
        amount_subunits=100000,
        rationale="One retry path.",
        requires_approval=False,
    )

    with pytest.raises(ValidationError, match="require approval"):
        RecoveryPlan(
            merchant_id=UUID("c56a4180-65aa-42ec-a945-5fd21dec0538"),
            incident_id=UUID("16fd2706-8baf-433b-82eb-8c7fada847da"),
            actions=[action],
            total_amount_subunits=100000,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
