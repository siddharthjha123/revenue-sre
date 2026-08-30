"""Behavior tests for crash recovery, retries, and durable job completion."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from prometheus_client import REGISTRY
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.database.base import Base
from backend.app.database.models import (
    PaymentAttempt,
    ProcessingJob,
    ProcessingJobStatus,
    WebhookEvent,
    WebhookStatus,
)
from backend.app.database.repositories.processing_job_repository import ProcessingJobRepository
from backend.app.database.repositories.webhook_repository import NewWebhookEvent, WebhookRepository
from backend.app.schemas.payment import PaymentStatus
from backend.app.workers.webhook_worker import (
    JobProcessingError,
    WebhookJobWorker,
    WorkerOutcome,
)

MERCHANT_ID = UUID("c56a4180-65aa-42ec-a945-5fd21dec0538")
CORRELATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@pytest_asyncio.fixture
async def session_factory() -> AsyncGenerator[async_sessionmaker[AsyncSession], None]:
    """Provide isolated sessions so worker transactions match production shape."""

    test_engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    yield factory
    await test_engine.dispose()


async def enqueue_event(
    factory: async_sessionmaker[AsyncSession],
    *,
    event_id: str = "event_worker_001",
    max_attempts: int = 5,
) -> None:
    """Atomically create an inbox event and its corresponding queue job."""

    async with factory() as session:
        async with session.begin():
            event = (
                await WebhookRepository(session).insert_once(
                    NewWebhookEvent(
                        correlation_id=CORRELATION_ID,
                        merchant_id=MERCHANT_ID,
                        razorpay_event_id=event_id,
                        razorpay_account_id="acc_TEST001",
                        event_type="payment.failed",
                        provider_event_at=datetime.fromtimestamp(1787664087, UTC),
                        payload_hash="a" * 64,
                        payload={
                            "entity": "event",
                            "account_id": "acc_TEST001",
                            "event": "payment.failed",
                            "created_at": 1787664087,
                            "contains": ["payment"],
                            "payload": {
                                "payment": {
                                    "entity": {
                                        "id": "pay_TU17WFeXwe0HQr",
                                        "order_id": "order_TU0xExample123",
                                        "amount": 100000,
                                        "currency": "INR",
                                        "status": "failed",
                                        "method": "netbanking",
                                        "captured": False,
                                        "international": False,
                                        "error_code": "BAD_REQUEST_ERROR",
                                        "error_source": "bank",
                                        "error_step": "payment_authorization",
                                        "error_reason": "payment_failed",
                                        "created_at": 1787664086,
                                    }
                                }
                            },
                        },
                    )
                )
            ).event
            await ProcessingJobRepository(session).enqueue_once(
                merchant_id=MERCHANT_ID,
                webhook_event_id=event.id,
                max_attempts=max_attempts,
            )


@pytest.mark.asyncio
async def test_worker_marks_job_and_event_successful(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await enqueue_event(session_factory)
    handled_events: list[str] = []

    async def handler(event: WebhookEvent, session: AsyncSession) -> None:
        handled_events.append(event.razorpay_event_id)

    result = await WebhookJobWorker(
        session_factory=session_factory,
        handler=handler,
        worker_id="worker-success",
    ).run_once()

    async with session_factory() as session:
        job = await session.scalar(select(ProcessingJob))
        event = await session.scalar(select(WebhookEvent))

    assert result.outcome == WorkerOutcome.SUCCEEDED
    assert handled_events == ["event_worker_001"]
    assert job is not None and job.status == ProcessingJobStatus.SUCCEEDED
    assert event is not None and event.status == WebhookStatus.PROCESSED


@pytest.mark.asyncio
async def test_worker_runs_real_payment_normalizer_atomically(
    session_factory: async_sessionmaker[AsyncSession],
    caplog: pytest.LogCaptureFixture,
) -> None:
    await enqueue_event(session_factory)
    with caplog.at_level("INFO"):
        result = await WebhookJobWorker(
            session_factory=session_factory,
            worker_id="worker-normalizer",
        ).run_once()

    async with session_factory() as session:
        payment = await session.scalar(select(PaymentAttempt))
        job = await session.scalar(select(ProcessingJob))
        event = await session.scalar(select(WebhookEvent))

    assert result.outcome == WorkerOutcome.SUCCEEDED
    assert payment is not None and payment.status == PaymentStatus.FAILED
    assert payment.payment_id == "pay_TU17WFeXwe0HQr"
    assert job is not None and job.status == ProcessingJobStatus.SUCCEEDED
    assert event is not None and event.status == WebhookStatus.PROCESSED
    processing_record = next(
        record
        for record in caplog.records
        if record.getMessage() == "Webhook job processing completed"
    )
    assert processing_record.correlation_id == str(CORRELATION_ID)
    assert processing_record.razorpay_event_id == "event_worker_001"
    assert processing_record.merchant_id == str(MERCHANT_ID)
    assert processing_record.event_type == "payment.failed"
    assert processing_record.processing_status == "succeeded"
    assert processing_record.duration_ms >= 0


@pytest.mark.asyncio
async def test_retryable_failure_retries_then_dead_letters(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await enqueue_event(session_factory, max_attempts=2)

    async def failing_handler(event: WebhookEvent, session: AsyncSession) -> None:
        raise JobProcessingError(
            code="temporary_database_dependency",
            safe_message="Temporary processing dependency unavailable",
            retryable=True,
        )

    worker = WebhookJobWorker(
        session_factory=session_factory,
        handler=failing_handler,
        worker_id="worker-retry",
        retry_base_seconds=1,
    )
    first = await worker.run_once()

    async with session_factory() as session:
        async with session.begin():
            job = await session.scalar(select(ProcessingJob).with_for_update())
            assert job is not None
            assert job.status == ProcessingJobStatus.RETRY_SCHEDULED
            assert job.last_error_code == "temporary_database_dependency"
            job.available_at = datetime.now(UTC) - timedelta(seconds=1)

    second = await worker.run_once()
    async with session_factory() as session:
        job = await session.scalar(select(ProcessingJob))
        event = await session.scalar(select(WebhookEvent))

    assert first.outcome == WorkerOutcome.RETRY_SCHEDULED
    assert second.outcome == WorkerOutcome.DEAD_LETTERED
    assert job is not None and job.attempt_count == 2
    assert job.status == ProcessingJobStatus.DEAD_LETTER
    assert event is not None and event.status == WebhookStatus.FAILED


@pytest.mark.asyncio
async def test_non_retryable_failure_dead_letters_immediately(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await enqueue_event(session_factory)
    failures_before = REGISTRY.get_sample_value("webhook_processing_failures_total") or 0
    durations_before = REGISTRY.get_sample_value("webhook_processing_duration_seconds_count") or 0

    async def invalid_handler(event: WebhookEvent, session: AsyncSession) -> None:
        raise JobProcessingError(
            code="invalid_payment_payload",
            safe_message="Payment payload cannot be normalized",
            retryable=False,
        )

    result = await WebhookJobWorker(
        session_factory=session_factory,
        handler=invalid_handler,
        worker_id="worker-invalid",
    ).run_once()

    async with session_factory() as session:
        job = await session.scalar(select(ProcessingJob))

    assert result.outcome == WorkerOutcome.DEAD_LETTERED
    assert job is not None and job.status == ProcessingJobStatus.DEAD_LETTER
    assert job.last_error_code == "invalid_payment_payload"
    assert REGISTRY.get_sample_value("webhook_processing_failures_total") == failures_before + 1
    assert (
        REGISTRY.get_sample_value("webhook_processing_duration_seconds_count")
        == durations_before + 1
    )


@pytest.mark.asyncio
async def test_worker_returns_no_job_when_queue_is_empty(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async def handler(event: WebhookEvent, session: AsyncSession) -> None:
        raise AssertionError("Empty queue must not invoke the handler")

    result = await WebhookJobWorker(
        session_factory=session_factory,
        handler=handler,
        worker_id="worker-empty",
    ).run_once()

    assert result.outcome == WorkerOutcome.NO_JOB
