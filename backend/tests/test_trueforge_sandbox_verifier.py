"""Contract tests for the dependency-free TrueForge sandbox verifier."""

import importlib.util
from copy import deepcopy
from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture(scope="module")
def verifier() -> ModuleType:
    path = (
        Path(__file__).parents[2]
        / "trueforge"
        / "skills"
        / "revenue-incident-investigator"
        / "scripts"
        / "verify_incident.py"
    )
    spec = importlib.util.spec_from_file_location("sandbox_incident_verifier", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def investigation() -> dict:
    return {
        "incident": {
            "incident_id": "93aa4387-16d2-4128-95bc-367433adce35",
            "baseline_attempt_count": 5,
            "baseline_failure_count": 0,
            "current_attempt_count": 5,
            "current_failure_count": 3,
            "baseline_failure_rate": 0.0,
            "current_failure_rate": 0.6,
            "revenue_at_risk_subunits": 60_000,
        },
        "evidence": [
            *[
                {
                    "evidence_id": f"fact-{index}",
                    "kind": "razorpay_fact",
                    "details": {
                        "payment_id": f"pay-{index}",
                        "amount_subunits": amount,
                    },
                }
                for index, amount in enumerate((10_000, 20_000, 30_000), start=1)
            ],
            {
                "evidence_id": "metric-1",
                "kind": "sandbox_metric",
                "details": {
                    "current_attempt_count": 5,
                    "current_failure_count": 3,
                    "failure_rate_increase": 0.6,
                    "revenue_at_risk_subunits": 60_000,
                },
            },
        ],
    }


def test_valid_investigation_is_independently_verified(verifier, investigation) -> None:
    result = verifier.verify_investigation(investigation)

    assert result["verified"] is True
    assert result["computed"]["current_failure_rate"] == 0.6
    assert result["computed"]["revenue_at_risk_subunits"] == 60_000
    assert result["computed"]["failed_payment_count"] == 3
    assert len(result["evidence_ids"]) == 4


@pytest.mark.parametrize(
    ("field", "value", "failed_check"),
    [
        ("current_failure_rate", 0.4, "current_failure_rate"),
        ("revenue_at_risk_subunits", 70_000, "money_at_risk"),
    ],
)
def test_tampered_incident_fails_closed(
    verifier,
    investigation,
    field,
    value,
    failed_check,
) -> None:
    payload = deepcopy(investigation)
    payload["incident"][field] = value

    result = verifier.verify_investigation(payload)

    assert result["verified"] is False
    assert failed_check in {check["name"] for check in result["checks"] if not check["passed"]}


def test_sensitive_fields_fail_sandbox_verification(verifier, investigation) -> None:
    payload = deepcopy(investigation)
    payload["evidence"][0]["details"]["customer_email"] = "hidden@example.com"

    result = verifier.verify_investigation(payload)

    assert result["verified"] is False
    pii_check = next(check for check in result["checks"] if check["name"] == "pii_and_secret_scan")
    assert pii_check["passed"] is False
    assert pii_check["actual"] == ["$.evidence[0].details.customer_email"]


def test_invalid_counts_are_rejected(verifier, investigation) -> None:
    payload = deepcopy(investigation)
    payload["incident"]["current_failure_count"] = 6

    with pytest.raises(verifier.VerificationInputError, match="cannot exceed"):
        verifier.verify_investigation(payload)
