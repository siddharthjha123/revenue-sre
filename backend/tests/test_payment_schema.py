"""Payment contract tests based on Razorpay test-mode shapes."""

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from backend.app.schemas.payment import PaymentAttemptResponse


def valid_payment() -> dict:
    return {
        "merchant_id": UUID("c56a4180-65aa-42ec-a945-5fd21dec0538"),
        "payment_id": "pay_TU17WFeXwe0HQr",
        "amount_subunits": 100000,
        "currency": "INR",
        "status": "captured",
        "method": "netbanking",
        "created_at": datetime.now(UTC),
        "captured": True,
    }


def test_accepts_realistic_razorpay_identifiers_and_subunits() -> None:
    payment = PaymentAttemptResponse.model_validate(valid_payment())

    assert payment.payment_id == "pay_TU17WFeXwe0HQr"
    assert payment.amount_subunits == 100000
    assert payment.method.value == "netbanking"


@pytest.mark.parametrize(
    ("field", "bad_value"),
    [("payment_id", "not-a-payment"), ("amount_subunits", 0), ("currency", "inr")],
)
def test_rejects_invalid_provider_data(field: str, bad_value: object) -> None:
    payload = valid_payment()
    payload[field] = bad_value

    with pytest.raises(ValidationError):
        PaymentAttemptResponse.model_validate(payload)


def test_rejects_naive_timestamps_and_unknown_fields() -> None:
    payload = valid_payment()
    payload["created_at"] = datetime.now()
    payload["unexpected"] = "unsafe schema drift"

    with pytest.raises(ValidationError):
        PaymentAttemptResponse.model_validate(payload)
