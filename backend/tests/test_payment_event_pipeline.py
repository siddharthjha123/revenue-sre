"""Regression tests for payment event pipeline orchestration."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.database.base import Base
from backend.app.database.models.incident import Incident
from backend.app.database.models.webhook_event import WebhookEvent, WebhookStatus
from backend.app.services.payment_event_pipeline import PaymentEventPipeline
from backend.scripts.load_demo_incident import build_demo_events, event_payload
from backend.tests.incident_test_support import MERCHANT_A, _event, detector_settings


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as value:
        yield value
    await engine.dispose()


class DetectorSpy:
    def __init__(self) -> None:
        self.calls = 0

    async def detect(self, *_args, **_kwargs) -> None:
        self.calls += 1


@pytest.mark.asyncio
async def test_only_failed_payments_advance_incident_detection(
    session: AsyncSession,
) -> None:
    """Recovery captures must not slide or rewrite existing incident windows."""

    pipeline = PaymentEventPipeline(detector_settings())
    detector = DetectorSpy()
    pipeline._detector = detector  # type: ignore[assignment]

    captured = _event(
        merchant_id=MERCHANT_A,
        event_index=100,
        status="captured",
        occurred_at=datetime(2026, 9, 5, 21, 30, tzinfo=UTC),
        payment_suffix="RECOVERYCAPTURE",
        amount=100_000,
    )
    session.add(captured)
    await session.flush()
    await pipeline(captured, session)

    assert detector.calls == 0

    failed = _event(
        merchant_id=MERCHANT_A,
        event_index=101,
        status="failed",
        occurred_at=datetime(2026, 9, 5, 21, 31, tzinfo=UTC),
        payment_suffix="NEWFAILURE",
        amount=100_000,
    )
    session.add(failed)
    await session.flush()
    await pipeline(failed, session)

    assert detector.calls == 1


@pytest.mark.asyncio
async def test_recovery_capture_preserves_both_demo_incident_snapshots(
    session: AsyncSession,
) -> None:
    """Reproduce the two-incident demo and guard against the former 8/8 regression."""

    anchor = datetime(2026, 9, 5, 21, 30, tzinfo=UTC)
    pipeline = PaymentEventPipeline(detector_settings())
    for index, demo_event in enumerate(build_demo_events(anchor, "regression")):
        event = WebhookEvent(
            correlation_id=MERCHANT_A,
            merchant_id=MERCHANT_A,
            razorpay_event_id=f"event_pipeline_regression_{index}",
            razorpay_account_id="acc_TEST001",
            event_type=f"payment.{demo_event.status}",
            provider_event_at=demo_event.occurred_at,
            payload_hash=f"{index:064x}",
            status=WebhookStatus.RECEIVED,
            payload=event_payload(demo_event, "acc_TEST001"),
        )
        session.add(event)
        await session.flush()
        await pipeline(event, session)

    before = {
        incident.bank: (
            incident.current_attempt_count,
            incident.current_failure_count,
            incident.revenue_at_risk_subunits,
        )
        for incident in (await session.scalars(select(Incident))).all()
    }
    assert before == {"HDFC": (20, 12, 1_200_000), "AXIS": (20, 10, 750_000)}

    recovery_capture = _event(
        merchant_id=MERCHANT_A,
        event_index=999,
        status="captured",
        occurred_at=anchor + timedelta(minutes=10),
        payment_suffix="RECOVERYAFTERDEMO",
        amount=100_000,
    )
    session.add(recovery_capture)
    await session.flush()
    await pipeline(recovery_capture, session)

    after = {
        incident.bank: (
            incident.current_attempt_count,
            incident.current_failure_count,
            incident.revenue_at_risk_subunits,
        )
        for incident in (await session.scalars(select(Incident))).all()
    }
    assert after == before
