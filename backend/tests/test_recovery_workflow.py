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
    RecoveryProposalAction,
)
from backend.app.database.models.webhook_event import WebhookEvent
from backend.app.database.repositories.incident_repository import (
    IncidentMetrics,
    IncidentRepository,
)
from backend.app.schemas.audit import ApprovalDecisionType, AuditEventType
from backend.app.schemas.incident import IncidentStatus
from backend.app.schemas.recovery import (
    RecoveryActionCreate,
    RecoveryExecutionStatus,
    RecoveryPlanStatus,
    RecoveryProposalCreate,
)
from backend.app.services.razorpay_mcp_adapter import PaymentLinkResult, RazorpayMCPError
from backend.app.services.recovery_outcome_service import RecoveryOutcomeService
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


def proposal_request(
    *, amount: int = 10_000, payment_id: str = "pay_CURF0"
) -> RecoveryProposalCreate:
    return RecoveryProposalCreate(
        actions=[
            RecoveryActionCreate(
                payment_id=payment_id,
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


@pytest.mark.asyncio
async def test_proposal_eligibility_excludes_stale_incident_evidence(
    session: AsyncSession,
) -> None:
    settings = detector_settings()
    incident = await seed_failure_spike(session, settings=settings)
    from backend.app.database.models.payment_event_fact import PaymentEventFact

    stale_fact = await session.scalar(
        select(PaymentEventFact).where(PaymentEventFact.payment_id == "pay_CURF0")
    )
    assert stale_fact is not None
    stale_fact.provider_event_at = incident.current_window_start - timedelta(seconds=1)
    await session.flush()

    proposal = await RecoveryService(settings).create_proposal(
        session,
        merchant_id=MERCHANT_A,
        incident_id=incident.id,
        request=proposal_request(amount=20_000, payment_id="pay_CURF1"),
        correlation_id=CORRELATION_ID,
    )

    assert proposal.eligible_payment_count == 2
    assert proposal.omitted_payment_count == 1


@pytest.mark.asyncio
async def test_detector_cannot_rewrite_incident_snapshot_after_proposal(
    session: AsyncSession,
) -> None:
    settings = detector_settings()
    incident = await seed_failure_spike(session, settings=settings)
    original_snapshot = (
        incident.current_window_start,
        incident.current_window_end,
        incident.current_attempt_count,
        incident.current_failure_count,
        incident.current_failure_rate,
        incident.revenue_at_risk_subunits,
    )
    await RecoveryService(settings).create_proposal(
        session,
        merchant_id=MERCHANT_A,
        incident_id=incident.id,
        request=proposal_request(),
        correlation_id=CORRELATION_ID,
    )

    shifted_end = incident.current_window_end + timedelta(minutes=15)
    updated, created = await IncidentRepository(session).upsert_detected(
        MERCHANT_A,
        IncidentMetrics(
            fingerprint=incident.fingerprint,
            incident_type=incident.incident_type,
            currency=incident.currency,
            method=incident.method,
            bank=incident.bank,
            error_reason=incident.error_reason,
            detector_version=incident.detector_version,
            baseline_window_start=shifted_end - timedelta(minutes=35),
            current_window_start=shifted_end - timedelta(minutes=5),
            current_window_end=shifted_end,
            baseline_attempt_count=8,
            baseline_failure_count=1,
            current_attempt_count=8,
            current_failure_count=8,
            baseline_failure_rate=0.125,
            current_failure_rate=1.0,
            revenue_at_risk_subunits=80_000,
            confidence=0.99,
        ),
    )

    assert created is False
    assert (
        updated.current_window_start,
        updated.current_window_end,
        updated.current_attempt_count,
        updated.current_failure_count,
        updated.current_failure_rate,
        updated.revenue_at_risk_subunits,
    ) == original_snapshot


class FakePaymentLinkAdapter:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def create_payment_link(self, arguments: dict) -> PaymentLinkResult:
        self.calls.append(arguments)
        return PaymentLinkResult(
            payment_link_id="plink_test_recovery",
            short_url="https://rzp.io/i/test-recovery",
            reference_id=arguments["reference_id"],
        )


class TransientlyFailingPaymentLinkAdapter(FakePaymentLinkAdapter):
    async def create_payment_link(self, arguments: dict) -> PaymentLinkResult:
        self.calls.append(arguments)
        if len(self.calls) == 1:
            raise RazorpayMCPError("temporary provider failure")
        return PaymentLinkResult(
            payment_link_id="plink_test_retried",
            short_url="https://rzp.io/i/test-retried",
            reference_id=arguments["reference_id"],
        )


@pytest.mark.asyncio
async def test_execution_retries_one_transient_provider_failure(
    session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def no_wait(_seconds: float) -> None:
        return None

    monkeypatch.setattr("backend.app.services.recovery_service.asyncio.sleep", no_wait)
    settings = detector_settings(execution_enabled=True)
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
    adapter = TransientlyFailingPaymentLinkAdapter()

    result = await service.execute_proposal(
        session,
        merchant_id=MERCHANT_A,
        proposal_id=proposal.proposal_id,
        executed_by="merchant-owner",
        correlation_id=CORRELATION_ID,
        adapter=adapter,
    )

    assert result.status == RecoveryPlanStatus.COMPLETED
    assert result.executed_count == 1
    assert result.failed_count == 0
    assert len(adapter.calls) == 2


@pytest.mark.asyncio
async def test_approved_proposal_executes_only_restricted_payment_link_payload(
    session: AsyncSession,
) -> None:
    settings = detector_settings(execution_enabled=True)
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
        reason="Execute the reviewed test-mode plan.",
        correlation_id=CORRELATION_ID,
    )
    adapter = FakePaymentLinkAdapter()

    result = await service.execute_proposal(
        session,
        merchant_id=MERCHANT_A,
        proposal_id=proposal.proposal_id,
        executed_by="merchant-owner",
        correlation_id=CORRELATION_ID,
        adapter=adapter,
    )

    assert result.status == RecoveryPlanStatus.COMPLETED
    assert result.executed_count == 1
    assert result.execution_performed is True
    assert len(adapter.calls) == 1
    payload = adapter.calls[0]
    assert payload["amount"] == 10_000
    assert payload["currency"] == "INR"
    assert payload["accept_partial"] is False
    assert payload["notify_sms"] is False
    assert payload["notify_email"] is False
    assert "customer_name" not in payload
    assert "customer_email" not in payload
    assert "customer_contact" not in payload
    assert len(payload["reference_id"]) <= 40
    action = await session.scalar(
        select(RecoveryProposalAction).where(
            RecoveryProposalAction.proposal_id == proposal.proposal_id
        )
    )
    assert action is not None
    assert action.execution_status == RecoveryExecutionStatus.SUCCEEDED
    assert action.provider_payment_link_id == "plink_test_recovery"
    events = (await session.scalars(select(AuditRecord.event_type))).all()
    assert AuditEventType.ACTION_EXECUTED in events

    replay = await service.execute_proposal(
        session,
        merchant_id=MERCHANT_A,
        proposal_id=proposal.proposal_id,
        executed_by="merchant-owner",
        correlation_id=CORRELATION_ID,
        adapter=adapter,
    )
    assert replay.executed_count == 1
    assert len(adapter.calls) == 1

    session.add(
        event := WebhookEvent(
            correlation_id=CORRELATION_ID,
            merchant_id=MERCHANT_A,
            razorpay_event_id="event_payment_link_paid_test",
            razorpay_account_id="acc_TEST001",
            event_type="payment_link.paid",
            provider_event_at=datetime.now(UTC),
            payload_hash="f" * 64,
            payload={
                "payload": {
                    "payment": {"entity": {"id": "pay_recovered_test"}},
                    "payment_link": {
                        "entity": {
                            "id": "plink_test_recovery",
                            "reference_id": payload["reference_id"],
                            "status": "paid",
                            "currency": "INR",
                            "amount_paid": 10_000,
                            "notes": payload["notes"],
                        }
                    },
                }
            },
        )
    )
    await session.flush()
    await RecoveryOutcomeService().record_payment_link_paid(event, session)
    await session.refresh(action)
    await session.refresh(incident)
    assert action.recovered_payment_id == "pay_recovered_test"
    assert action.recovered_amount_subunits == 10_000
    assert action.recovered_at is not None
    assert incident.status == IncidentStatus.INVESTIGATING
    events = (await session.scalars(select(AuditRecord.event_type))).all()
    assert AuditEventType.OUTCOME_VERIFIED in events
