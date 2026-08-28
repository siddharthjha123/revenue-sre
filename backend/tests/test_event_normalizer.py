"""Tests for Razorpay payment mapping and current-state projection rules."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.database.base import Base
from backend.app.database.models import PaymentAttempt, WebhookEvent
from backend.app.database.repositories.webhook_repository import NewWebhookEvent, WebhookRepository
from backend.app.schemas.payment import PaymentMethod, PaymentStatus
from backend.app.services.event_normalizer import (
    PaymentEventNormalizer,
    PaymentProjectionAction,
    normalize_payment_event,
)

MERCHANT_ID = UUID("c56a4180-65aa-42ec-a945-5fd21dec0538")
CORRELATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
PAYMENT_CREATED_AT = 1787664086


def as_utc(value: datetime) -> datetime:
    """Restore UTC metadata that SQLite omits from timestamp round trips."""

    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


@pytest_asyncio.fixture
async def database_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a fresh current-state projection database for each test."""

    test_engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with test_engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(test_engine, expire_on_commit=False)
    async with factory() as session:
        yield session
    await test_engine.dispose()


def webhook_event(
    *,
    event_id: str,
    event_type: str,
    status: str,
    event_created_at: int,
    method: str = "netbanking",
) -> WebhookEvent:
    """Build the PII-minimized shape stored by the webhook endpoint."""

    failed = status == "failed"
    return WebhookEvent(
        correlation_id=CORRELATION_ID,
        merchant_id=MERCHANT_ID,
        razorpay_event_id=event_id,
        razorpay_account_id="acc_TEST001",
        event_type=event_type,
        provider_event_at=datetime.fromtimestamp(event_created_at, UTC),
        payload_hash="a" * 64,
        payload={
            "entity": "event",
            "account_id": "acc_TEST001",
            "event": event_type,
            "created_at": event_created_at,
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": "pay_TU17WFeXwe0HQr",
                        "order_id": "order_TU0xExample123",
                        "amount": 100000,
                        "currency": "INR",
                        "status": status,
                        "method": method,
                        "captured": status == "captured",
                        "international": False,
                        "error_code": "BAD_REQUEST_ERROR" if failed else None,
                        "error_description": "Payment failed at bank" if failed else None,
                        "error_source": "bank" if failed else None,
                        "error_step": "payment_authorization" if failed else None,
                        "error_reason": "payment_failed" if failed else None,
                        "created_at": PAYMENT_CREATED_AT,
                    }
                }
            },
        },
    )


async def store_event(session: AsyncSession, event: WebhookEvent) -> WebhookEvent:
    """Store a generated event through the same idempotent inbox repository."""

    return (
        await WebhookRepository(session).insert_once(
            NewWebhookEvent(
                correlation_id=event.correlation_id,
                merchant_id=event.merchant_id,
                razorpay_event_id=event.razorpay_event_id,
                razorpay_account_id=event.razorpay_account_id,
                event_type=event.event_type,
                provider_event_at=event.provider_event_at,
                payload_hash=event.payload_hash,
                payload=event.payload,
            )
        )
    ).event


@pytest.mark.parametrize(
    ("event_type", "status"),
    [
        ("payment.failed", "failed"),
        ("payment.authorized", "authorized"),
        ("payment.captured", "captured"),
    ],
)
def test_supported_events_map_to_payment_response(event_type: str, status: str) -> None:
    normalized = normalize_payment_event(
        webhook_event(
            event_id="event_map_001",
            event_type=event_type,
            status=status,
            event_created_at=1787664087,
        )
    )

    assert normalized.payment_id == "pay_TU17WFeXwe0HQr"
    assert normalized.order_id == "order_TU0xExample123"
    assert normalized.amount_subunits == 100000
    assert normalized.currency == "INR"
    assert normalized.status == PaymentStatus(status)
    assert normalized.method == PaymentMethod.NETBANKING
    assert normalized.created_at == datetime.fromtimestamp(PAYMENT_CREATED_AT, UTC)
    assert normalized.created_at.tzinfo is not None


def test_unknown_payment_method_normalizes_to_other() -> None:
    event = webhook_event(
        event_id="event_method_001",
        event_type="payment.authorized",
        status="authorized",
        event_created_at=1787664087,
        method="future_pay_method",
    )

    assert normalize_payment_event(event).method == PaymentMethod.OTHER


@pytest.mark.asyncio
async def test_failed_then_captured_preserves_events_and_updates_current_state(
    database_session: AsyncSession,
) -> None:
    normalizer = PaymentEventNormalizer()
    failed = await store_event(
        database_session,
        webhook_event(
            event_id="event_failed_001",
            event_type="payment.failed",
            status="failed",
            event_created_at=1787664087,
        ),
    )
    captured = await store_event(
        database_session,
        webhook_event(
            event_id="event_captured_001",
            event_type="payment.captured",
            status="captured",
            event_created_at=1787664088,
        ),
    )

    first_action = await normalizer.normalize_and_persist(failed, database_session)
    second_action = await normalizer.normalize_and_persist(captured, database_session)
    payment = await database_session.scalar(select(PaymentAttempt))
    event_count = await database_session.scalar(select(func.count()).select_from(WebhookEvent))

    assert first_action == PaymentProjectionAction.CREATED
    assert second_action == PaymentProjectionAction.UPDATED
    assert event_count == 2
    assert payment is not None
    assert payment.status == PaymentStatus.CAPTURED
    assert payment.captured is True
    assert payment.last_razorpay_event_id == "event_captured_001"
    assert as_utc(payment.last_applied_event_at) == datetime.fromtimestamp(1787664088, UTC)
    assert payment.error_code is None
    assert payment.error_source is None


@pytest.mark.asyncio
async def test_captured_payment_cannot_regress_when_older_failure_arrives_later(
    database_session: AsyncSession,
) -> None:
    normalizer = PaymentEventNormalizer()
    captured = await store_event(
        database_session,
        webhook_event(
            event_id="event_captured_002",
            event_type="payment.captured",
            status="captured",
            event_created_at=1787664090,
        ),
    )
    late_failed = await store_event(
        database_session,
        webhook_event(
            event_id="event_failed_late",
            event_type="payment.failed",
            status="failed",
            event_created_at=1787664088,
        ),
    )

    await normalizer.normalize_and_persist(captured, database_session)
    action = await normalizer.normalize_and_persist(late_failed, database_session)
    payment = await database_session.scalar(select(PaymentAttempt))

    event_count = await database_session.scalar(select(func.count()).select_from(WebhookEvent))

    assert action == PaymentProjectionAction.IGNORED_REGRESSION
    assert event_count == 2
    assert payment is not None and payment.status == PaymentStatus.CAPTURED
    assert payment.last_razorpay_event_id == "event_captured_002"
    assert as_utc(payment.last_applied_event_at) == datetime.fromtimestamp(1787664090, UTC)
    assert captured.provider_event_at.replace(tzinfo=UTC) == datetime.fromtimestamp(1787664090, UTC)
    assert late_failed.provider_event_at.replace(tzinfo=UTC) == datetime.fromtimestamp(
        1787664088, UTC
    )


@pytest.mark.asyncio
async def test_older_but_valid_forward_transition_is_applied(
    database_session: AsyncSession,
) -> None:
    normalizer = PaymentEventNormalizer()
    failed = await store_event(
        database_session,
        webhook_event(
            event_id="event_failed_newer_clock",
            event_type="payment.failed",
            status="failed",
            event_created_at=1787664090,
        ),
    )
    delayed_captured = await store_event(
        database_session,
        webhook_event(
            event_id="event_captured_delayed",
            event_type="payment.captured",
            status="captured",
            event_created_at=1787664088,
        ),
    )

    await normalizer.normalize_and_persist(failed, database_session)
    action = await normalizer.normalize_and_persist(delayed_captured, database_session)
    payment = await database_session.scalar(select(PaymentAttempt))

    assert action == PaymentProjectionAction.UPDATED
    assert payment is not None and payment.status == PaymentStatus.CAPTURED
    assert as_utc(payment.last_applied_event_at) == datetime.fromtimestamp(1787664088, UTC)


@pytest.mark.asyncio
async def test_older_same_state_event_is_stored_but_not_applied(
    database_session: AsyncSession,
) -> None:
    normalizer = PaymentEventNormalizer()
    newer_failed = await store_event(
        database_session,
        webhook_event(
            event_id="event_failed_newer",
            event_type="payment.failed",
            status="failed",
            event_created_at=1787664090,
        ),
    )
    older_failed = await store_event(
        database_session,
        webhook_event(
            event_id="event_failed_older",
            event_type="payment.failed",
            status="failed",
            event_created_at=1787664088,
        ),
    )

    await normalizer.normalize_and_persist(newer_failed, database_session)
    action = await normalizer.normalize_and_persist(older_failed, database_session)
    payment = await database_session.scalar(select(PaymentAttempt))

    assert action == PaymentProjectionAction.IGNORED_STALE
    assert payment is not None
    assert payment.last_razorpay_event_id == "event_failed_newer"
    assert as_utc(payment.last_applied_event_at) == datetime.fromtimestamp(1787664090, UTC)


@pytest.mark.asyncio
async def test_replaying_same_event_is_idempotent(database_session: AsyncSession) -> None:
    normalizer = PaymentEventNormalizer()
    event = await store_event(
        database_session,
        webhook_event(
            event_id="event_replay_001",
            event_type="payment.failed",
            status="failed",
            event_created_at=1787664087,
        ),
    )

    await normalizer.normalize_and_persist(event, database_session)
    replay_action = await normalizer.normalize_and_persist(event, database_session)
    count = await database_session.scalar(select(func.count()).select_from(PaymentAttempt))

    assert replay_action == PaymentProjectionAction.IGNORED_REPLAY
    assert count == 1
