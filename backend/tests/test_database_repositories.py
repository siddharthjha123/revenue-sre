"""Isolated async tests for the Step 1 persistence layer."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.database.base import Base
from backend.app.database.models import (
    PaymentAttempt,
    ProcessingJob,
    ProcessingJobStatus,
    WebhookEvent,
)
from backend.app.database.repositories.payment_repository import PaymentRepository
from backend.app.database.repositories.processing_job_repository import (
    JobLeaseLostError,
    ProcessingJobRepository,
)
from backend.app.database.repositories.webhook_repository import (
    NewWebhookEvent,
    WebhookRepository,
    WebhookTenantMismatchError,
)

MERCHANT_A = UUID("c56a4180-65aa-42ec-a945-5fd21dec0538")
MERCHANT_B = UUID("c56a4180-65aa-42ec-a945-5fd21dec0539")
CORRELATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")


@pytest_asyncio.fixture
async def database_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh in-memory database without contacting PostgreSQL."""

    test_engine = create_async_engine(
        "sqlite+aiosqlite://",
        poolclass=StaticPool,
    )
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with session_factory() as session:
        yield session

    await test_engine.dispose()


def new_event(*, merchant_id: UUID = MERCHANT_A) -> NewWebhookEvent:
    """Return one sanitized event suitable for repository tests."""

    return NewWebhookEvent(
        correlation_id=CORRELATION_ID,
        merchant_id=merchant_id,
        razorpay_event_id="event_test_001",
        razorpay_account_id="acc_test_001",
        event_type="payment.failed",
        provider_event_at=datetime.now(UTC),
        payload_hash="a" * 64,
        payload={
            "event": "payment.failed",
            "payment": {"id": "pay_TU17WFeXwe0HQr", "status": "failed"},
        },
    )


def payment(*, merchant_id: UUID = MERCHANT_A) -> PaymentAttempt:
    """Return one normalized payment without customer-identifying fields."""

    occurred_at = datetime.now(UTC)
    return PaymentAttempt(
        merchant_id=merchant_id,
        razorpay_account_id="acc_test_001",
        payment_id="pay_TU17WFeXwe0HQr",
        order_id="order_TU0xExample123",
        amount_subunits=100000,
        currency="INR",
        status="failed",
        method="netbanking",
        captured=False,
        international=False,
        error_code="BAD_REQUEST_ERROR",
        error_source="bank",
        error_step="payment_authorization",
        error_reason="payment_failed",
        last_razorpay_event_id="event_test_001",
        provider_created_at=occurred_at,
        last_applied_event_at=occurred_at,
    )


@pytest.mark.asyncio
async def test_webhook_insert_is_idempotent(database_session: AsyncSession) -> None:
    repository = WebhookRepository(database_session)

    first = await repository.insert_once(new_event())
    duplicate = await repository.insert_once(new_event())
    count = await database_session.scalar(select(func.count()).select_from(WebhookEvent))

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.event.id == first.event.id
    assert count == 1


@pytest.mark.asyncio
async def test_duplicate_event_cannot_cross_tenants(database_session: AsyncSession) -> None:
    repository = WebhookRepository(database_session)
    await repository.insert_once(new_event())

    with pytest.raises(WebhookTenantMismatchError):
        await repository.insert_once(new_event(merchant_id=MERCHANT_B))


@pytest.mark.asyncio
async def test_processing_job_is_enqueued_once_and_completed(
    database_session: AsyncSession,
) -> None:
    event = (await WebhookRepository(database_session).insert_once(new_event())).event
    repository = ProcessingJobRepository(database_session)
    first = await repository.enqueue_once(merchant_id=MERCHANT_A, webhook_event_id=event.id)
    duplicate = await repository.enqueue_once(merchant_id=MERCHANT_A, webhook_event_id=event.id)

    claimed = await repository.claim_next(worker_id="worker-1")

    assert first.created is True
    assert duplicate.created is False
    assert duplicate.job.id == first.job.id
    assert claimed is not None
    assert claimed.attempt_count == 1
    job = await repository.get_active_lease_for_update(claimed)
    assert job.status == ProcessingJobStatus.PROCESSING
    assert job.locked_by == "worker-1"

    await repository.mark_succeeded(job)

    assert job.status == ProcessingJobStatus.SUCCEEDED
    assert job.completed_at is not None
    assert job.locked_by is None


@pytest.mark.asyncio
async def test_expired_processing_job_can_be_reclaimed(
    database_session: AsyncSession,
) -> None:
    event = (await WebhookRepository(database_session).insert_once(new_event())).event
    repository = ProcessingJobRepository(database_session)
    insertion = await repository.enqueue_once(
        merchant_id=MERCHANT_A,
        webhook_event_id=event.id,
    )
    first_claim = await repository.claim_next(worker_id="crashed-worker", lease_seconds=60)
    assert first_claim is not None

    insertion.job.lease_expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await database_session.flush()
    replacement_claim = await repository.claim_next(worker_id="replacement-worker")

    assert replacement_claim is not None
    assert replacement_claim.job_id == first_claim.job_id
    assert replacement_claim.lease_token != first_claim.lease_token
    assert replacement_claim.attempt_count == 2

    with pytest.raises(JobLeaseLostError):
        await repository.get_active_lease_for_update(first_claim)


@pytest.mark.asyncio
async def test_two_workers_claim_different_available_jobs(
    database_session: AsyncSession,
) -> None:
    webhook_repository = WebhookRepository(database_session)
    first_event = (await webhook_repository.insert_once(new_event())).event
    second_values = new_event()
    second_values = NewWebhookEvent(
        correlation_id=second_values.correlation_id,
        merchant_id=second_values.merchant_id,
        razorpay_event_id="event_test_002",
        razorpay_account_id=second_values.razorpay_account_id,
        event_type=second_values.event_type,
        provider_event_at=second_values.provider_event_at,
        payload_hash=second_values.payload_hash,
        payload=second_values.payload,
    )
    second_event = (await webhook_repository.insert_once(second_values)).event
    repository = ProcessingJobRepository(database_session)
    await repository.enqueue_once(merchant_id=MERCHANT_A, webhook_event_id=first_event.id)
    await repository.enqueue_once(merchant_id=MERCHANT_A, webhook_event_id=second_event.id)

    first_claim = await repository.claim_next(worker_id="worker-1")
    second_claim = await repository.claim_next(worker_id="worker-2")
    count = await database_session.scalar(select(func.count()).select_from(ProcessingJob))

    assert first_claim is not None
    assert second_claim is not None
    assert first_claim.job_id != second_claim.job_id
    assert count == 2


@pytest.mark.asyncio
async def test_payment_repository_enforces_tenant_reads(
    database_session: AsyncSession,
) -> None:
    repository = PaymentRepository(database_session)
    stored = await repository.add(payment())

    own_payment = await repository.get_by_payment_id(MERCHANT_A, stored.payment_id)
    another_tenant = await repository.get_by_payment_id(MERCHANT_B, stored.payment_id)

    assert own_payment is stored
    assert another_tenant is None


@pytest.mark.asyncio
async def test_payment_model_excludes_customer_pii(database_session: AsyncSession) -> None:
    repository = PaymentRepository(database_session)
    await repository.add(payment())

    columns = set(PaymentAttempt.__table__.columns.keys())

    assert "email" not in columns
    assert "contact" not in columns
    assert len(await repository.list_recent(MERCHANT_A)) == 1
