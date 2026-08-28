"""API tests for authenticated, idempotent Razorpay webhook ingestion."""

import hashlib
import hmac
import json
import logging
from collections.abc import AsyncGenerator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import httpx
import pytest
import pytest_asyncio
from prometheus_client import REGISTRY
from pydantic import SecretStr
from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.config import Settings, get_settings
from backend.app.database.base import Base, get_db_session
from backend.app.database.models import ProcessingJob, WebhookEvent, WebhookStatus
from backend.app.database.repositories.processing_job_repository import ProcessingJobRepository
from backend.app.main import app

SECRET = "endpoint-test-webhook-secret"
MERCHANT_ID = UUID("c56a4180-65aa-42ec-a945-5fd21dec0538")
ACCOUNT_ID = "acc_TEST001"


@dataclass(slots=True)
class WebhookTestHarness:
    client: httpx.AsyncClient
    session: AsyncSession
    settings: Settings


@pytest_asyncio.fixture
async def webhook_harness() -> AsyncGenerator[WebhookTestHarness, None]:
    """Run the API and persistence layer against a fresh in-memory database."""

    test_engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        database_url="sqlite+aiosqlite://",
        razorpay_webhook_secret=SecretStr(SECRET),
        merchant_id=MERCHANT_ID,
        razorpay_account_id=ACCOUNT_ID,
    )

    async with session_factory() as session:

        async def override_session() -> AsyncGenerator[AsyncSession, None]:
            yield session

        def override_settings() -> Settings:
            return settings

        app.dependency_overrides[get_db_session] = override_session
        app.dependency_overrides[get_settings] = override_settings
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield WebhookTestHarness(client=client, session=session, settings=settings)

    app.dependency_overrides.clear()
    await test_engine.dispose()


def payment_event(
    *,
    event: str = "payment.failed",
    account_id: str = ACCOUNT_ID,
) -> dict:
    return {
        "entity": "event",
        "account_id": account_id,
        "event": event,
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_TU17WFeXwe0HQr",
                    "entity": "payment",
                    "amount": 100000,
                    "currency": "INR",
                    "status": "failed",
                    "order_id": "order_TU0xExample123",
                    "method": "netbanking",
                    "captured": False,
                    "international": False,
                    "bank": "BARB_R",
                    "email": "must-not-be-stored@example.invalid",
                    "contact": "+919999999999",
                    "notes": {"untrusted": "customer text"},
                    "error_code": "BAD_REQUEST_ERROR",
                    "error_source": "bank",
                    "error_step": "payment_authorization",
                    "error_reason": "payment_failed",
                    "created_at": 1787664086,
                }
            }
        },
        "created_at": 1787664087,
    }


def encode(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":")).encode()


def signature_for(raw_body: bytes) -> str:
    return hmac.new(SECRET.encode(), raw_body, hashlib.sha256).hexdigest()


async def post_webhook(
    harness: WebhookTestHarness,
    raw_body: bytes,
    *,
    event_id: str = "event_test_001",
    signature: str | None = None,
) -> httpx.Response:
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": event_id,
    }
    if signature is not None:
        headers["X-Razorpay-Signature"] = signature
    return await harness.client.post(
        "/webhooks/razorpay",
        content=raw_body,
        headers=headers,
    )


@pytest.mark.asyncio
async def test_valid_event_is_authenticated_sanitized_and_stored(
    webhook_harness: WebhookTestHarness,
) -> None:
    raw_body = encode(payment_event())

    response = await post_webhook(
        webhook_harness,
        raw_body,
        signature=signature_for(raw_body),
    )
    stored = await webhook_harness.session.scalar(select(WebhookEvent))
    job = await webhook_harness.session.scalar(select(ProcessingJob))

    assert response.status_code == 200
    assert response.json() == {"status": "accepted"}
    assert stored is not None
    assert job is not None
    assert job.webhook_event_id == stored.id
    assert job.merchant_id == stored.merchant_id
    assert stored.status == WebhookStatus.RECEIVED
    assert stored.provider_event_at.replace(tzinfo=UTC) == datetime.fromtimestamp(1787664087, UTC)
    assert stored.payload_hash == hashlib.sha256(raw_body).hexdigest()
    assert response.headers["X-Correlation-ID"] == str(stored.correlation_id)
    assert "email" not in str(stored.payload)
    assert "contact" not in str(stored.payload)
    assert "notes" not in str(stored.payload)


@pytest.mark.asyncio
async def test_customer_email_and_contact_are_not_written_to_logs(
    webhook_harness: WebhookTestHarness,
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_body = encode(payment_event())

    with caplog.at_level(logging.INFO):
        response = await post_webhook(
            webhook_harness,
            raw_body,
            signature=signature_for(raw_body),
        )

    serialized_records = "\n".join(str(record.__dict__) for record in caplog.records)
    ingestion_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "Razorpay webhook ingestion completed"
    )

    assert response.status_code == 200
    assert "must-not-be-stored@example.invalid" not in serialized_records
    assert "+919999999999" not in serialized_records
    assert ingestion_record.correlation_id == response.headers["X-Correlation-ID"]
    assert ingestion_record.razorpay_event_id == "event_test_001"
    assert ingestion_record.merchant_id == str(MERCHANT_ID)
    assert ingestion_record.event_type == "payment.failed"
    assert ingestion_record.processing_status == "accepted"
    assert ingestion_record.duration_ms >= 0


@pytest.mark.asyncio
async def test_duplicate_event_is_acknowledged_once(
    webhook_harness: WebhookTestHarness,
) -> None:
    raw_body = encode(payment_event())
    signature = signature_for(raw_body)
    duplicate_before = REGISTRY.get_sample_value("webhooks_duplicate_total") or 0
    received_before = REGISTRY.get_sample_value("webhooks_received_total") or 0

    first = await post_webhook(webhook_harness, raw_body, signature=signature)
    duplicate = await post_webhook(webhook_harness, raw_body, signature=signature)
    count = await webhook_harness.session.scalar(select(func.count()).select_from(WebhookEvent))
    job_count = await webhook_harness.session.scalar(
        select(func.count()).select_from(ProcessingJob)
    )

    assert first.json() == {"status": "accepted"}
    assert duplicate.json() == {"status": "duplicate"}
    assert count == 1
    assert job_count == 1
    assert REGISTRY.get_sample_value("webhooks_duplicate_total") == duplicate_before + 1
    assert REGISTRY.get_sample_value("webhooks_received_total") == received_before + 2


@pytest.mark.asyncio
async def test_missing_and_invalid_signatures_store_nothing(
    webhook_harness: WebhookTestHarness,
) -> None:
    raw_body = encode(payment_event())
    invalid_before = REGISTRY.get_sample_value("webhooks_invalid_signature_total") or 0

    missing = await post_webhook(webhook_harness, raw_body)
    invalid = await post_webhook(webhook_harness, raw_body, signature="0" * 64)
    count = await webhook_harness.session.scalar(select(func.count()).select_from(WebhookEvent))

    assert missing.status_code == 400
    assert invalid.status_code == 401
    assert count == 0
    assert REGISTRY.get_sample_value("webhooks_invalid_signature_total") == invalid_before + 2


@pytest.mark.asyncio
async def test_signed_malformed_json_is_rejected(
    webhook_harness: WebhookTestHarness,
) -> None:
    raw_body = b'{"event":'

    response = await post_webhook(
        webhook_harness,
        raw_body,
        signature=signature_for(raw_body),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Malformed webhook JSON"}


@pytest.mark.asyncio
async def test_missing_event_id_is_rejected(
    webhook_harness: WebhookTestHarness,
) -> None:
    raw_body = encode(payment_event())

    response = await webhook_harness.client.post(
        "/webhooks/razorpay",
        content=raw_body,
        headers={"X-Razorpay-Signature": signature_for(raw_body)},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Missing or invalid Razorpay event ID"}


@pytest.mark.asyncio
async def test_supported_event_requires_payment_entity(
    webhook_harness: WebhookTestHarness,
) -> None:
    payload = payment_event()
    payload["payload"] = {}
    raw_body = encode(payload)

    response = await post_webhook(
        webhook_harness,
        raw_body,
        signature=signature_for(raw_body),
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid payment webhook payload"}


@pytest.mark.asyncio
async def test_wrong_razorpay_account_is_rejected(
    webhook_harness: WebhookTestHarness,
) -> None:
    raw_body = encode(payment_event(account_id="acc_OTHER001"))

    response = await post_webhook(
        webhook_harness,
        raw_body,
        signature=signature_for(raw_body),
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_unsupported_event_is_stored_as_ignored(
    webhook_harness: WebhookTestHarness,
) -> None:
    payload = payment_event(event="refund.created")
    payload["payload"] = {"refund": {"entity": {"id": "rfnd_TEST001"}}}
    raw_body = encode(payload)

    response = await post_webhook(
        webhook_harness,
        raw_body,
        signature=signature_for(raw_body),
    )
    stored = await webhook_harness.session.scalar(select(WebhookEvent))
    job_count = await webhook_harness.session.scalar(
        select(func.count()).select_from(ProcessingJob)
    )

    assert response.json() == {"status": "ignored"}
    assert stored is not None
    assert stored.status == WebhookStatus.IGNORED
    assert job_count == 0
    assert "payload" not in stored.payload


@pytest.mark.asyncio
async def test_event_and_job_roll_back_together_when_enqueue_fails(
    webhook_harness: WebhookTestHarness,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fail_enqueue(*args: object, **kwargs: object) -> None:
        raise SQLAlchemyError("forced queue failure")

    monkeypatch.setattr(ProcessingJobRepository, "enqueue_once", fail_enqueue)
    raw_body = encode(payment_event())

    response = await post_webhook(
        webhook_harness,
        raw_body,
        signature=signature_for(raw_body),
    )
    event_count = await webhook_harness.session.scalar(
        select(func.count()).select_from(WebhookEvent)
    )
    job_count = await webhook_harness.session.scalar(
        select(func.count()).select_from(ProcessingJob)
    )

    assert response.status_code == 503
    assert event_count == 0
    assert job_count == 0


@pytest.mark.asyncio
async def test_provider_event_id_cannot_move_between_merchants(
    webhook_harness: WebhookTestHarness,
) -> None:
    raw_body = encode(payment_event())
    signature = signature_for(raw_body)
    first = await post_webhook(webhook_harness, raw_body, signature=signature)
    webhook_harness.settings.merchant_id = UUID("c56a4180-65aa-42ec-a945-5fd21dec0539")

    conflict = await post_webhook(webhook_harness, raw_body, signature=signature)

    assert first.status_code == 200
    assert conflict.status_code == 409
    assert conflict.json() == {"detail": "Webhook event identity conflict"}


@pytest.mark.asyncio
async def test_oversized_payload_is_rejected_before_authentication(
    webhook_harness: WebhookTestHarness,
) -> None:
    webhook_harness.settings.webhook_max_body_bytes = 1024
    raw_body = b"x" * 1025

    response = await post_webhook(webhook_harness, raw_body, signature="0" * 64)

    assert response.status_code == 413


@pytest.mark.asyncio
async def test_missing_server_secret_fails_closed(
    webhook_harness: WebhookTestHarness,
) -> None:
    webhook_harness.settings.razorpay_webhook_secret = None
    raw_body = encode(payment_event())

    response = await post_webhook(
        webhook_harness,
        raw_body,
        signature=signature_for(raw_body),
    )

    assert response.status_code == 503
    assert response.json() == {"detail": "Webhook authentication is unavailable"}
