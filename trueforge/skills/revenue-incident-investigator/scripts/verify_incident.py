#!/usr/bin/env python3
"""Independently verify a PII-safe Revenue SRE incident investigation.

This script intentionally uses only the Python standard library so a TrueForge
sandbox can execute it without installing project dependencies or receiving
backend credentials.
"""

from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any

MAX_INPUT_BYTES = 1_000_000
SENSITIVE_KEY_PARTS = (
    "api_key",
    "authorization",
    "contact",
    "email",
    "password",
    "recovery_url",
    "secret",
    "token",
)


class VerificationInputError(ValueError):
    """Raised when sandbox input is missing, oversized, or structurally invalid."""


def verify_investigation(payload: dict[str, Any]) -> dict[str, Any]:
    """Recalculate incident metrics and compare them with persisted evidence."""

    incident = _mapping(payload, "incident")
    evidence = _list(payload, "evidence")
    checks: list[dict[str, Any]] = []

    baseline_attempts = _integer(incident, "baseline_attempt_count")
    baseline_failures = _integer(incident, "baseline_failure_count")
    current_attempts = _integer(incident, "current_attempt_count")
    current_failures = _integer(incident, "current_failure_count")
    risk_subunits = _integer(incident, "revenue_at_risk_subunits")
    baseline_rate = _number(incident, "baseline_failure_rate")
    current_rate = _number(incident, "current_failure_rate")

    computed_baseline_rate = _rate(baseline_failures, baseline_attempts)
    computed_current_rate = _rate(current_failures, current_attempts)
    computed_rate_increase = computed_current_rate - computed_baseline_rate

    fact_evidence = [item for item in evidence if _kind(item) == "razorpay_fact"]
    metric_evidence = [item for item in evidence if _kind(item) == "sandbox_metric"]
    unique_payment_ids = {
        str(_details(item).get("payment_id"))
        for item in fact_evidence
        if _details(item).get("payment_id")
    }
    fact_risk = sum(_integer(_details(item), "amount_subunits") for item in fact_evidence)

    _check_close(checks, "baseline_failure_rate", baseline_rate, computed_baseline_rate)
    _check_close(checks, "current_failure_rate", current_rate, computed_current_rate)
    _check_equal(checks, "failed_evidence_count", current_failures, len(unique_payment_ids))
    _check_equal(checks, "money_at_risk", risk_subunits, fact_risk)
    _check_equal(checks, "one_metric_snapshot", 1, len(metric_evidence))

    if metric_evidence:
        metric = _details(metric_evidence[0])
        _check_equal(
            checks,
            "metric_current_attempt_count",
            current_attempts,
            metric.get("current_attempt_count"),
        )
        _check_equal(
            checks,
            "metric_current_failure_count",
            current_failures,
            metric.get("current_failure_count"),
        )
        _check_close(
            checks,
            "metric_failure_rate_increase",
            computed_rate_increase,
            metric.get("failure_rate_increase"),
        )
        _check_equal(
            checks,
            "metric_money_at_risk",
            risk_subunits,
            metric.get("revenue_at_risk_subunits"),
        )

    sensitive_paths = _find_sensitive_paths(payload)
    _check_equal(checks, "pii_and_secret_scan", [], sensitive_paths)
    evidence_ids = sorted(
        str(item["evidence_id"])
        for item in evidence
        if isinstance(item, dict) and item.get("evidence_id")
    )
    verified = bool(checks) and all(check["passed"] for check in checks)
    return {
        "verified": verified,
        "incident_id": str(incident.get("incident_id", "")),
        "computed": {
            "baseline_failure_rate": computed_baseline_rate,
            "current_failure_rate": computed_current_rate,
            "failure_rate_increase": computed_rate_increase,
            "revenue_at_risk_subunits": fact_risk,
            "failed_payment_count": len(unique_payment_ids),
        },
        "checks": checks,
        "evidence_ids": evidence_ids,
        "limitations": [
            "Evidence consistency does not prove a provider's internal root cause.",
            "Verification does not authorize or execute a recovery action.",
        ],
    }


def _load(path: str | None) -> dict[str, Any]:
    if path:
        raw = Path(path).read_bytes()
    else:
        raw = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise VerificationInputError("Input exceeds the one-megabyte safety limit")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise VerificationInputError("Input must be valid UTF-8 JSON") from error
    if not isinstance(value, dict):
        raise VerificationInputError("Input root must be a JSON object")
    return value


def _mapping(value: dict[str, Any], key: str) -> dict[str, Any]:
    result = value.get(key)
    if not isinstance(result, dict):
        raise VerificationInputError(f"{key} must be an object")
    return result


def _list(value: dict[str, Any], key: str) -> list[Any]:
    result = value.get(key)
    if not isinstance(result, list):
        raise VerificationInputError(f"{key} must be an array")
    return result


def _details(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict) or not isinstance(item.get("details"), dict):
        raise VerificationInputError("Every evidence item must contain details")
    return item["details"]


def _kind(item: Any) -> str:
    if not isinstance(item, dict):
        raise VerificationInputError("Every evidence item must be an object")
    return str(item.get("kind", ""))


def _integer(value: dict[str, Any], key: str) -> int:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, int) or result < 0:
        raise VerificationInputError(f"{key} must be a non-negative integer")
    return result


def _number(value: dict[str, Any], key: str) -> float:
    result = value.get(key)
    if isinstance(result, bool) or not isinstance(result, (int, float)):
        raise VerificationInputError(f"{key} must be numeric")
    number = float(result)
    if not math.isfinite(number) or not 0 <= number <= 1:
        raise VerificationInputError(f"{key} must be between zero and one")
    return number


def _rate(failures: int, attempts: int) -> float:
    if failures > attempts:
        raise VerificationInputError("Failure count cannot exceed attempt count")
    return failures / attempts if attempts else 0.0


def _check_equal(checks: list[dict[str, Any]], name: str, expected: Any, actual: Any) -> None:
    checks.append(
        {"name": name, "passed": expected == actual, "expected": expected, "actual": actual}
    )


def _check_close(checks: list[dict[str, Any]], name: str, expected: float, actual: Any) -> None:
    passed = (
        not isinstance(actual, bool)
        and isinstance(actual, (int, float))
        and math.isfinite(float(actual))
        and math.isclose(expected, float(actual), rel_tol=1e-9, abs_tol=1e-9)
    )
    checks.append({"name": name, "passed": passed, "expected": expected, "actual": actual})


def _find_sensitive_paths(value: Any, path: str = "$") -> list[str]:
    found: list[str] = []
    if isinstance(value, dict):
        for key, nested in value.items():
            child_path = f"{path}.{key}"
            normalized = str(key).lower()
            if any(part in normalized for part in SENSITIVE_KEY_PARTS):
                found.append(child_path)
            found.extend(_find_sensitive_paths(nested, child_path))
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found.extend(_find_sensitive_paths(nested, f"{path}[{index}]"))
    return found


def main() -> int:
    try:
        result = verify_investigation(_load(sys.argv[1] if len(sys.argv) > 1 else None))
    except (OSError, VerificationInputError) as error:
        print(json.dumps({"verified": False, "error": str(error)}))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["verified"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
