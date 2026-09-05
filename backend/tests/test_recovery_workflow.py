"""Safety proofs for policy-gated proposals and immutable decisions."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import pytest
import pytest_asyncio
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.database.base import Base
from backend.app.database.models.recovery import (
    ApprovalRecord,
    AuditRecord,
    ImmutableApprovalRecordError,
    RecoveryProposal,
)
from backend.app.schemas.audit import ApprovalDecisionType, AuditEventType
from backend.app.schemas.recovery import (
    RecoveryActionCreate,
    RecoveryPlanStatus,
    RecoveryProposalCreate,
)
from backend.app.services.recovery_service import (
    RecoveryConflictError,
    RecoveryPolicyRejectedError,
    RecoveryService,
)
from backend.tests.incident_test_support import (
    CORRELATION_ID,
    MERCHANT_A,
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


def proposal_request(*, amount: int = 10_000) -> RecoveryProposalCreate:
    return RecoveryProposalCreate(
        actions=[
            RecoveryActionCreate(
                payment_id="pay_CURF0",
                action_type="create_payment_link",
                amount_subunits=amount,
                rationale="Offer one merchant-approved retry path.",
            )
        ],
        expires_at=datetime.now(UTC) + timedelta(minutes=30),
        created_by="trueforge-agent",
    )


@pytest.mark.asyncio
async def test_allowed_proposal_can_be_approved_once_without_execution(
    session: AsyncSession,
) -> None:
    settings = detector_settings()
    incident = await seed_failure_spike(session, settings=settings)
    service = RecoveryService(settings)
    proposal = await service.create_proposal(
        session,
        merchant_id=MERCHANT_A,
        incident_id=incident.id,
        request=proposal_request(),
        correlation_id=CORRELATION_ID,
    )

    decision = await service.decide(
        session,
        merchant_id=MERCHANT_A,
        proposal_id=proposal.proposal_id,
        decision=ApprovalDecisionType.APPROVED,
        decided_by="merchant-owner",
        reason="Approved for a bounded test recovery.",
        correlation_id=CORRELATION_ID,
    )
    replay = await service.decide(
        session,
        merchant_id=MERCHANT_A,
        proposal_id=proposal.proposal_id,
        decision=ApprovalDecisionType.APPROVED,
        decided_by="merchant-owner",
        reason="Approved for a bounded test recovery.",
        correlation_id=CORRELATION_ID,
    )

    stored = await session.get(RecoveryProposal, proposal.proposal_id)
    approval_count = await session.scalar(select(func.count()).select_from(ApprovalRecord))
    events = set((await session.scalars(select(AuditRecord.event_type))).all())
    assert proposal.policy_allowed is True
    assert proposal.status == RecoveryPlanStatus.PENDING_APPROVAL
    assert len(proposal.evidence_ids) == 4
    assert stored is not None and stored.status == RecoveryPlanStatus.APPROVED
    assert decision.approval_id == replay.approval_id
    assert decision.plan_hash == proposal.content_hash
    assert decision.reason == "Approved for a bounded test recovery."
    assert approval_count == 1
    assert {
        AuditEventType.PLAN_PROPOSED,
        AuditEventType.POLICY_VALIDATED,
        AuditEventType.APPROVAL_REQUESTED,
        AuditEventType.PLAN_APPROVED,
    }.issubset(events)
    assert not hasattr(stored, "executed_at")


@pytest.mark.asyncio
async def test_approval_record_cannot_be_updated_or_deleted(session: AsyncSession) -> None:
    settings = detector_settings()
    incident = await seed_failure_spike(session, settings=settings)
    service = RecoveryService(settings)
    proposal = await service.create_proposal(
        session,
        merchant_id=MERCHANT_A,
        incident_id=incident.id,
        request=proposal_request(),
        correlation_id=CORRELATION_ID,
    )
    decision = await service.decide(
        session,
        merchant_id=MERCHANT_A,
        proposal_id=proposal.proposal_id,
        decision=ApprovalDecisionType.REJECTED,
        decided_by="merchant-owner",
        reason="Merchant chose not to contact customers.",
        correlation_id=CORRELATION_ID,
    )
    record = await session.get(ApprovalRecord, decision.approval_id)
    assert record is not None
    await session.commit()

    record.reason = "Tampered reason"
    with pytest.raises(ImmutableApprovalRecordError, match="append-only"):
        await session.flush()
    await session.rollback()

    record = await session.get(ApprovalRecord, decision.approval_id)
    assert record is not None
    await session.delete(record)
    with pytest.raises(ImmutableApprovalRecordError, match="append-only"):
        await session.flush()
    await session.rollback()


@pytest.mark.asyncio
async def test_conflicting_second_decision_is_rejected(session: AsyncSession) -> None:
    settings = detector_settings()
    incident = await seed_failure_spike(session, settings=settings)
    service = RecoveryService(settings)
    proposal = await service.create_proposal(
        session,
        merchant_id=MERCHANT_A,
        incident_id=incident.id,
        request=proposal_request(),
        correlation_id=CORRELATION_ID,
    )
    await service.decide(
        session,
        merchant_id=MERCHANT_A,
        proposal_id=proposal.proposal_id,
        decision=ApprovalDecisionType.APPROVED,
        decided_by="merchant-owner",
        reason=None,
        correlation_id=CORRELATION_ID,
    )

    with pytest.raises(RecoveryConflictError, match="different decision"):
        await service.decide(
            session,
            merchant_id=MERCHANT_A,
            proposal_id=proposal.proposal_id,
            decision=ApprovalDecisionType.REJECTED,
            decided_by="merchant-owner",
            reason="Conflicting replay must fail.",
            correlation_id=CORRELATION_ID,
        )


@pytest.mark.asyncio
async def test_policy_rejected_proposal_cannot_be_approved(session: AsyncSession) -> None:
    settings = detector_settings(recovery_max_plan_amount_subunits=5_000)
    incident = await seed_failure_spike(session, settings=settings)
    service = RecoveryService(settings)
    proposal = await service.create_proposal(
        session,
        merchant_id=MERCHANT_A,
        incident_id=incident.id,
        request=proposal_request(amount=10_000),
        correlation_id=CORRELATION_ID,
    )

    assert proposal.policy_allowed is False
    assert proposal.status == RecoveryPlanStatus.DRAFT
    assert "proposal exceeds the configured amount limit" in proposal.policy_reasons
    with pytest.raises(RecoveryConflictError, match="not awaiting approval"):
        await service.decide(
            session,
            merchant_id=MERCHANT_A,
            proposal_id=proposal.proposal_id,
            decision=ApprovalDecisionType.APPROVED,
            decided_by="merchant-owner",
            reason=None,
            correlation_id=CORRELATION_ID,
        )


@pytest.mark.asyncio
async def test_approval_rechecks_payment_is_still_unpaid(session: AsyncSession) -> None:
    settings = detector_settings()
    incident = await seed_failure_spike(session, settings=settings)
    service = RecoveryService(settings)
    proposal = await service.create_proposal(
        session,
        merchant_id=MERCHANT_A,
        incident_id=incident.id,
        request=proposal_request(),
        correlation_id=CORRELATION_ID,
    )
    from backend.app.database.models.payment_attempt import PaymentAttempt
    from backend.app.schemas.payment import PaymentStatus

    payment = await session.scalar(
        select(PaymentAttempt).where(PaymentAttempt.payment_id == "pay_CURF0")
    )
    assert payment is not None
    payment.status = PaymentStatus.CAPTURED
    await session.flush()

    with pytest.raises(RecoveryPolicyRejectedError, match="no longer passes"):
        await service.decide(
            session,
            merchant_id=MERCHANT_A,
            proposal_id=proposal.proposal_id,
            decision=ApprovalDecisionType.APPROVED,
            decided_by="merchant-owner",
            reason=None,
            correlation_id=CORRELATION_ID,
        )
