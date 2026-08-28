"""Safety and exposure tests for structured logs, correlation, and metrics."""

import json
import logging
from uuid import UUID

import httpx
import pytest

from backend.app.main import app
from backend.app.observability.logging import StructuredJsonFormatter


def test_structured_formatter_includes_required_fields_and_drops_secrets() -> None:
    record = logging.LogRecord(
        name="backend.test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="Webhook processed",
        args=(),
        exc_info=None,
    )
    record.correlation_id = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
    record.razorpay_event_id = "event_test_001"
    record.merchant_id = "c56a4180-65aa-42ec-a945-5fd21dec0538"
    record.event_type = "payment.failed"
    record.processing_status = "succeeded"
    record.duration_ms = 12.5
    record.authorization = "Bearer must-never-appear"
    record.webhook_secret = "super-secret"
    record.customer_email = "private@example.invalid"
    record.customer_contact = "+919999999999"
    record.full_recovery_url = "https://example.invalid/recovery/private-token"

    document = json.loads(StructuredJsonFormatter().format(record))
    serialized = json.dumps(document)

    assert document["correlation_id"] == record.correlation_id
    assert document["razorpay_event_id"] == record.razorpay_event_id
    assert document["merchant_id"] == record.merchant_id
    assert document["event_type"] == record.event_type
    assert document["processing_status"] == record.processing_status
    assert document["duration_ms"] == 12.5
    assert "must-never-appear" not in serialized
    assert "super-secret" not in serialized
    assert "private@example.invalid" not in serialized
    assert "+919999999999" not in serialized
    assert "private-token" not in serialized


@pytest.mark.asyncio
async def test_metrics_endpoint_exposes_required_webhook_metrics() -> None:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert "webhooks_received_total" in response.text
    assert "webhooks_duplicate_total" in response.text
    assert "webhooks_invalid_signature_total" in response.text
    assert "webhook_processing_failures_total" in response.text
    assert "webhook_processing_duration_seconds" in response.text
    UUID(response.headers["X-Correlation-ID"])


@pytest.mark.asyncio
async def test_valid_correlation_id_is_returned_unchanged() -> None:
    correlation_id = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get(
            "/health",
            headers={"X-Correlation-ID": correlation_id},
        )

    assert response.headers["X-Correlation-ID"] == correlation_id
