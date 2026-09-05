"""Deterministic contract tests for the signed demo incident batch."""

import hashlib
import hmac
from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from backend.scripts.load_demo_incident import (
    EXPECTED_EVENT_COUNT,
    EXPECTED_REVENUE_AT_RISK_SUBUNITS,
    EXPECTED_SECONDARY_REVENUE_AT_RISK_SUBUNITS,
    SECONDARY_TARGET_SEGMENT,
    TARGET_SEGMENT,
    build_demo_events,
    event_payload,
    sign,
)


def test_demo_batch_has_primary_sixty_percent_failure_spike() -> None:
    anchor = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    events = build_demo_events(anchor, "20260830120000")
    current_start = anchor - timedelta(minutes=5)
    target = [event for event in events if event.segment == TARGET_SEGMENT]
    baseline = [event for event in target if event.occurred_at < current_start]
    current = [event for event in target if event.occurred_at >= current_start]

    assert len(events) == EXPECTED_EVENT_COUNT
    assert len(baseline) == 40
    assert len([event for event in baseline if event.status == "failed"]) == 2
    assert len(current) == 20
    assert len([event for event in current if event.status == "failed"]) == 12
    assert len([event for event in current if event.status == "failed"]) / len(current) == 0.60
    assert (
        sum(event.amount_subunits for event in current if event.status == "failed")
        == EXPECTED_REVENUE_AT_RISK_SUBUNITS
    )


def test_demo_batch_has_secondary_fifty_percent_failure_spike() -> None:
    anchor = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    events = build_demo_events(anchor, "20260830120000")
    current_start = anchor - timedelta(minutes=5)
    target = [event for event in events if event.segment == SECONDARY_TARGET_SEGMENT]
    baseline = [event for event in target if event.occurred_at < current_start]
    current = [event for event in target if event.occurred_at >= current_start]

    assert len(events) == EXPECTED_EVENT_COUNT
    assert len(baseline) == 30
    assert len([event for event in baseline if event.status == "failed"]) == 1
    assert len(current) == 20
    assert len([event for event in current if event.status == "failed"]) == 10
    assert len([event for event in current if event.status == "failed"]) / len(current) == 0.50
    assert (
        sum(event.amount_subunits for event in current if event.status == "failed")
        == EXPECTED_SECONDARY_REVENUE_AT_RISK_SUBUNITS
    )


def test_demo_control_segments_stay_below_incident_thresholds() -> None:
    anchor = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    events = build_demo_events(anchor, "20260830120000")
    current_start = anchor - timedelta(minutes=5)

    controls = {event.segment for event in events} - {
        TARGET_SEGMENT,
        SECONDARY_TARGET_SEGMENT,
    }
    assert controls == {"card_icici_noise"}
    for segment in controls:
        current = [
            event
            for event in events
            if event.segment == segment and event.occurred_at >= current_start
        ]
        failures = [event for event in current if event.status == "failed"]
        assert len(failures) < 3
        assert len(failures) / len(current) < 0.50


def test_demo_events_are_unique_and_chronologically_ordered() -> None:
    events = build_demo_events(
        datetime(2026, 8, 30, 12, 0, tzinfo=UTC),
        "20260830120000",
    )

    assert len({event.payment_id for event in events}) == EXPECTED_EVENT_COUNT
    assert len({event.order_id for event in events}) == EXPECTED_EVENT_COUNT
    assert events == sorted(events, key=lambda event: (event.occurred_at, event.payment_id))


def test_demo_target_bank_can_rotate_without_changing_incident_math() -> None:
    anchor = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)
    events = build_demo_events(anchor, "run", target_bank="AXIS", secondary_bank="KOTAK")
    target = [event for event in events if event.segment == TARGET_SEGMENT]

    assert {event.bank for event in target} == {"AXIS"}
    assert len(target) == 60
    assert len([event for event in target if event.status == "failed"]) == 14


def test_demo_incident_banks_must_be_distinct() -> None:
    anchor = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    try:
        build_demo_events(anchor, "run", target_bank="AXIS", secondary_bank="AXIS")
    except ValueError as error:
        assert str(error) == "Demo incident banks must be distinct"
    else:
        raise AssertionError("Expected duplicate incident banks to be rejected")


def test_demo_payload_is_pii_free_and_signature_binds_exact_bytes() -> None:
    event = next(
        event
        for event in build_demo_events(datetime(2026, 8, 30, 12, 0, tzinfo=UTC), "run")
        if event.segment == TARGET_SEGMENT and event.status == "failed"
    )
    payload = event_payload(event, "acc_DEMO001")
    raw_body = b'{"demo":"body"}'
    secret = SecretStr("demo-secret")

    assert payload["event"] == "payment.failed"
    assert payload["payload"]["payment"]["entity"]["error_reason"] == "payment_timed_out"
    assert "email" not in str(payload)
    assert "contact" not in str(payload)
    assert (
        sign(raw_body, secret)
        == hmac.new(
            b"demo-secret",
            raw_body,
            hashlib.sha256,
        ).hexdigest()
    )
