"""Deterministic contract tests for the signed demo incident batch."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from backend.scripts.load_demo_incident import build_demo_events, event_payload, sign


def test_demo_batch_has_healthy_baseline_and_sixty_percent_current_failures() -> None:
    anchor = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    events = build_demo_events(anchor, "20260830120000")
    baseline = [event for event in events if event.occurred_at < anchor - timedelta(minutes=5)]
    current = [event for event in events if event.occurred_at >= anchor - timedelta(minutes=5)]

    assert len(events) == 10
    assert len(baseline) == 5
    assert all(event.status == "captured" for event in baseline)
    assert len(current) == 5
    assert len([event for event in current if event.status == "failed"]) == 3
    assert sum(event.amount_subunits for event in current if event.status == "failed") == 60_000


def test_demo_payload_is_pii_free_and_signature_binds_exact_bytes() -> None:
    event = build_demo_events(datetime(2026, 8, 30, 12, 0, tzinfo=UTC), "run")[7]
    payload = event_payload(event, "acc_DEMO001")
    raw_body = b'{"demo":"body"}'
    secret = SecretStr("demo-secret")

    assert payload["event"] == "payment.failed"
    assert payload["payload"]["payment"]["entity"]["error_reason"] == "payment_timed_out"
    assert "email" not in str(payload)
    assert "contact" not in str(payload)
    assert sign(raw_body, secret) == hmac.new(
        b"demo-secret",
        raw_body,
        hashlib.sha256,
    ).hexdigest()
