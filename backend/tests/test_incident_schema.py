"""Incident grouping and metric-boundary tests."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.app.schemas.incident import IncidentResponse


def test_incident_preserves_payment_ids_and_financial_units() -> None:
    incident = IncidentResponse(
        incident_id=UUID("16fd2706-8baf-433b-82eb-8c7fada847da"),
        merchant_id=UUID("c56a4180-65aa-42ec-a945-5fd21dec0538"),
        incident_type="payment_failure_spike",
        payment_ids=["pay_TU17WFeXwe0HQr"],
        revenue_at_risk_subunits=100000,
        baseline_success_rate=0.94,
        current_success_rate=0.61,
        started_at=datetime.now(UTC),
    )

    assert incident.payment_ids == ["pay_TU17WFeXwe0HQr"]
    assert incident.revenue_at_risk_subunits == 100000


def test_incident_rejects_percentage_outside_ratio_range() -> None:
    with pytest.raises(ValidationError):
        IncidentResponse(
            incident_id=UUID("16fd2706-8baf-433b-82eb-8c7fada847da"),
            merchant_id=UUID("c56a4180-65aa-42ec-a945-5fd21dec0538"),
            incident_type="payment_failure_spike",
            baseline_success_rate=94,
            started_at=datetime.now(UTC),
        )
