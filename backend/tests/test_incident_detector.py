"""End-to-end proofs for normalized facts, detection, and exact evidence."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.database.base import Base
from backend.app.database.models.incident import Incident, IncidentEvidenceRecord
from backend.app.database.models.payment_event_fact import PaymentEventFact
from backend.app.database.models.recovery import AuditRecord
from backend.app.schemas.audit import AuditEventType
from backend.app.schemas.incident import EvidenceKind
from backend.app.services.incident_detector import IncidentDetector
from backend.tests.incident_test_support import (
    CORRELATION_ID,
    MERCHANT_A,
    MERCHANT_B,
    detector_settings,
    seed_failure_spike,
)


@pytest_asyncio.fixture
async def session() -> AsyncGenerator[AsyncSession, None]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with AsyncSession(engine, expire_on_commit=False) as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_batch_automatically_creates_incident_with_exact_evidence(
    session: AsyncSession,
) -> None:
    incident = await seed_failure_spike(session)

    fact_count = await session.scalar(select(func.count()).select_from(PaymentEventFact))
    evidence = (
        await session.scalars(
            select(IncidentEvidenceRecord).where(IncidentEvidenceRecord.incident_id == incident.id)
        )
    ).all()
    audit = await session.scalar(select(AuditRecord).where(AuditRecord.incident_id == incident.id))

    assert fact_count == 10
    assert incident.current_attempt_count == 5
    assert incident.current_failure_count == 3
    assert incident.baseline_attempt_count == 5
    assert incident.baseline_failure_count == 0
    assert incident.current_failure_rate == pytest.approx(0.6)
    assert incident.baseline_failure_rate == 0
    assert incident.revenue_at_risk_subunits == 60_000
    assert len([item for item in evidence if item.kind == EvidenceKind.RAZORPAY_FACT]) == 3
    assert len([item for item in evidence if item.kind == EvidenceKind.SANDBOX_METRIC]) == 1
    assert {item.source_reference for item in evidence if item.payment_event_fact_id} == {
        "event_incident_0538_7",
        "event_incident_0538_8",
        "event_incident_0538_9",
    }
    assert audit is not None and audit.event_type == AuditEventType.INCIDENT_CREATED


@pytest.mark.asyncio
async def test_detector_rerun_updates_same_incident_without_duplicate_fact_evidence(
    session: AsyncSession,
) -> None:
    first = await seed_failure_spike(session)
    await IncidentDetector(detector_settings()).detect(
        session,
        merchant_id=MERCHANT_A,
        correlation_id=CORRELATION_ID,
    )
    second = await session.scalar(select(Incident).where(Incident.merchant_id == MERCHANT_A))
    fact_evidence_count = await session.scalar(
        select(func.count())
        .select_from(IncidentEvidenceRecord)
        .where(IncidentEvidenceRecord.payment_event_fact_id.is_not(None))
    )

    assert second is not None and second.id == first.id
    assert fact_evidence_count == 3


@pytest.mark.asyncio
async def test_incident_queries_remain_tenant_isolated(session: AsyncSession) -> None:
    incident = await seed_failure_spike(session)

    cross_tenant = await session.scalar(
        select(Incident).where(
            Incident.id == incident.id,
            Incident.merchant_id == MERCHANT_B,
        )
    )

    assert cross_tenant is None


def test_payment_fact_schema_excludes_customer_pii() -> None:
    columns = set(PaymentEventFact.__table__.columns.keys())

    assert "email" not in columns
    assert "contact" not in columns
    assert "card" not in columns
