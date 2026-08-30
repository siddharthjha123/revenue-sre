"""Shared test builders with no network or provider dependency."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.app.schemas.recovery import RecoveryAction, RecoveryPlan

MERCHANT_ID = UUID("c56a4180-65aa-42ec-a945-5fd21dec0538")
INCIDENT_ID = UUID("16fd2706-8baf-433b-82eb-8c7fada847da")


@pytest.fixture
def recovery_plan() -> RecoveryPlan:
    """Return a valid future-dated plan for deterministic policy tests."""

    action = RecoveryAction(
        payment_id="pay_TU17WFeXwe0HQr",
        action_type="create_payment_link",
        amount_subunits=100000,
        rationale="Offer one merchant-approved retry path.",
    )
    return RecoveryPlan(
        merchant_id=MERCHANT_ID,
        incident_id=INCIDENT_ID,
        actions=[action],
        total_amount_subunits=100000,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
