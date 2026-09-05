"""HTTP contract tests for real merchant dashboard aggregates."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.config import get_settings
from backend.app.database.base import Base, get_db_session
from backend.app.database.models.incident import Incident
from backend.app.database.models.payment_attempt import PaymentAttempt
from backend.app.database.models.recovery import RecoveryProposal, RecoveryProposalAction
from backend.app.main import create_app
from backend.app.schemas.incident import IncidentStatus, IncidentType
from backend.app.schemas.payment import PaymentMethod, PaymentStatus
from backend.app.schemas.recovery import (
    RecoveryActionType,
    RecoveryExecutionStatus,
    RecoveryPlanStatus,
)
from backend.tests.incident_test_support import MERCHANT_A, MERCHANT_B, detector_settings


@pytest_asyncio.fixture
async def dashboard_context():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(UTC)
    async with factory() as session:
        async with session.begin():
            open_incident = _incident("open", IncidentStatus.OPEN, 30_000)
            session.add_all(
                [
                    _payment("A1", PaymentStatus.CAPTURED, 10_000, now),
                    _payment("A2", PaymentStatus.CAPTURED, 20_000, now),
                    _payment("A3", PaymentStatus.FAILED, 30_000, now),
                    _payment(
                        "A4",
                        PaymentStatus.CAPTURED,
                        40_000,
                        now - timedelta(days=2),
                    ),
                    _payment("B1", PaymentStatus.CAPTURED, 9_999_999, now, MERCHANT_B),
                    open_incident,
                    _incident("investigating", IncidentStatus.INVESTIGATING, 20_000),
                    _incident("resolved", IncidentStatus.RESOLVED, 90_000),
                    _incident("other-merchant", IncidentStatus.OPEN, 9_999_999, MERCHANT_B),
                ]
            )
            await session.flush()
            proposal = RecoveryProposal(
                id=uuid4(),
                merchant_id=MERCHANT_A,
                incident_id=open_incident.id,
                status=RecoveryPlanStatus.COMPLETED,
                total_amount_subunits=10_000,
                currency="INR",
                maximum_customer_contacts=1,
                expires_at=now + timedelta(minutes=30),
                content_hash="a" * 64,
                policy_version="test-v1",
                policy_allowed=True,
                policy_reasons=[],
                eligible_payment_count=1,
                omitted_payment_count=0,
                evidence_ids=[],
                created_by="test-agent",
            )
            session.add(proposal)
            session.add(
                RecoveryProposalAction(
                    proposal_id=proposal.id,
                    payment_id="pay_A3",
                    action_type=RecoveryActionType.CREATE_PAYMENT_LINK,
                    amount_subunits=10_000,
                    rationale="Test verified recovery.",
                    requires_approval=True,
                    execution_status=RecoveryExecutionStatus.SUCCEEDED,
                    recovered_payment_id="pay_recovered_A3",
                    recovered_amount_subunits=10_000,
                    recovered_at=now,
                )
            )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    application = create_app()
    application.dependency_overrides[get_db_session] = override_session
    application.dependency_overrides[get_settings] = lambda: detector_settings()
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    application.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_dashboard_summary_uses_real_merchant_aggregates(dashboard_context) -> None:
    response = await dashboard_context.get(
        "/dashboard/summary",
        headers={"X-Merchant-Id": str(MERCHANT_A)},
    )

    assert response.status_code == 200
    summary = response.json()
    assert summary["total_payment_attempts"] == 4
    assert summary["captured_payment_count"] == 3
    assert summary["captured_revenue_today"] == [{"currency": "INR", "amount_subunits": 30_000}]
    assert summary["total_incident_count"] == 3
    assert summary["open_incident_count"] == 2
    assert summary["open_revenue_at_risk"] == [{"currency": "INR", "amount_subunits": 40_000}]
    assert summary["recovered_revenue_today"] == [{"currency": "INR", "amount_subunits": 10_000}]
    assert summary["reporting_timezone"] == "Asia/Kolkata"


@pytest.mark.asyncio
async def test_dashboard_summary_rejects_another_merchant(dashboard_context) -> None:
    response = await dashboard_context.get(
        "/dashboard/summary",
        headers={"X-Merchant-Id": str(MERCHANT_B)},
    )

    assert response.status_code == 403


def _payment(
    suffix: str,
    status: PaymentStatus,
    amount_subunits: int,
    occurred_at: datetime,
    merchant_id=MERCHANT_A,
) -> PaymentAttempt:
    return PaymentAttempt(
        merchant_id=merchant_id,
        razorpay_account_id="acc_TEST001",
        payment_id=f"pay_{suffix}",
        amount_subunits=amount_subunits,
        currency="INR",
        status=status,
        method=PaymentMethod.UPI,
        bank="HDFC",
        captured=status == PaymentStatus.CAPTURED,
        international=False,
        error_reason="payment_timed_out" if status == PaymentStatus.FAILED else None,
        last_razorpay_event_id=f"event_{suffix}",
        provider_created_at=occurred_at,
        last_applied_event_at=occurred_at,
    )


def _incident(
    suffix: str,
    status: IncidentStatus,
    revenue_at_risk_subunits: int,
    merchant_id=MERCHANT_A,
) -> Incident:
    now = datetime.now(UTC)
    return Incident(
        merchant_id=merchant_id,
        fingerprint=f"dashboard-{suffix}",
        incident_type=IncidentType.PAYMENT_FAILURE_SPIKE,
        status=status,
        currency="INR",
        method="upi",
        bank="HDFC",
        error_reason="payment_timed_out",
        detector_version="test-v1",
        baseline_window_start=now - timedelta(minutes=35),
        current_window_start=now - timedelta(minutes=5),
        current_window_end=now,
        baseline_attempt_count=40,
        baseline_failure_count=2,
        current_attempt_count=20,
        current_failure_count=12,
        baseline_failure_rate=0.05,
        current_failure_rate=0.6,
        revenue_at_risk_subunits=revenue_at_risk_subunits,
        confidence=0.97,
        opened_at=now,
        last_detected_at=now,
    )
