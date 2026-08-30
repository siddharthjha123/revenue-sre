"""End-to-end safety tests for the TrueForge proposal MCP bridge."""

from collections.abc import AsyncGenerator

import pytest
import pytest_asyncio
from mcp import Client
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.database.base import Base
from backend.app.database.models.recovery import RecoveryProposal
from backend.app.mcp import server
from backend.app.mcp.incident_tools import IncidentInvestigationTools, IncidentNotFoundError
from backend.app.mcp.recovery_tools import RecoveryAgentTools
from backend.app.schemas.audit import ApprovalDecisionType
from backend.app.schemas.recovery import RecoveryActionType, RecoveryPlanStatus
from backend.app.services.recovery_service import RecoveryService
from backend.tests.incident_test_support import (
    CORRELATION_ID,
    MERCHANT_A,
    MERCHANT_B,
    detector_settings,
    seed_failure_spike,
)


@pytest_asyncio.fixture
async def recovery_context() -> AsyncGenerator[tuple[async_sessionmaker, object], None]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = detector_settings()
    async with factory() as session:
        async with session.begin():
            incident = await seed_failure_spike(session, settings=settings)
    yield factory, incident
    await engine.dispose()


@pytest.mark.asyncio
async def test_agent_creates_evidence_bound_policy_reviewed_proposal(
    recovery_context,
) -> None:
    factory, incident = recovery_context
    settings = detector_settings()
    tools = RecoveryAgentTools(settings, factory)

    proposal = await tools.create_bounded_proposal(
        incident_id=incident.id,
        action_type=RecoveryActionType.CREATE_PAYMENT_LINK,
        rationale="Offer one approval-gated retry path for verified timeout failures.",
        expires_in_minutes=30,
    )
    retrieved = await tools.get_proposal(proposal.proposal_id)
    replay = await tools.create_bounded_proposal(
        incident_id=incident.id,
        action_type=RecoveryActionType.CREATE_PAYMENT_LINK,
        rationale="A repeated agent turn must return the active proposal.",
        expires_in_minutes=30,
    )

    assert proposal.policy_allowed is True
    assert proposal.status == RecoveryPlanStatus.PENDING_APPROVAL
    assert proposal.total_amount_subunits == 60_000
    assert len(proposal.actions) == 3
    assert len(proposal.evidence_ids) == 4
    assert retrieved.content_hash == proposal.content_hash
    assert retrieved.status == proposal.status
    assert {action.action_id for action in retrieved.actions} == {
        action.action_id for action in proposal.actions
    }
    assert replay.proposal_id == proposal.proposal_id
    async with factory() as session:
        proposal_count = await session.scalar(select(func.count()).select_from(RecoveryProposal))
    assert proposal_count == 1


@pytest.mark.asyncio
async def test_merchant_approval_changes_status_but_executes_nothing(
    recovery_context,
) -> None:
    factory, incident = recovery_context
    settings = detector_settings()
    tools = RecoveryAgentTools(settings, factory)
    proposal = await tools.create_bounded_proposal(
        incident_id=incident.id,
        action_type=RecoveryActionType.ALLOW_CUSTOMER_RETRY,
        rationale="Allow one bounded customer retry after merchant approval.",
        expires_in_minutes=30,
    )

    async with factory() as session:
        async with session.begin():
            await RecoveryService(settings).decide(
                session,
                merchant_id=MERCHANT_A,
                proposal_id=proposal.proposal_id,
                decision=ApprovalDecisionType.APPROVED,
                decided_by="merchant-owner",
                correlation_id=CORRELATION_ID,
            )

    approved = await tools.get_proposal(proposal.proposal_id)
    assert approved.status == RecoveryPlanStatus.APPROVED
    assert not hasattr(approved, "execution_result")


@pytest.mark.asyncio
async def test_agent_cannot_cross_merchant_boundary(recovery_context) -> None:
    factory, incident = recovery_context
    tools = RecoveryAgentTools(detector_settings(merchant_id=MERCHANT_B), factory)

    with pytest.raises(IncidentNotFoundError, match="Incident was not found"):
        await tools.create_bounded_proposal(
            incident_id=incident.id,
            action_type=RecoveryActionType.CREATE_PAYMENT_LINK,
            rationale="This must never cross the tenant boundary.",
            expires_in_minutes=30,
        )


@pytest.mark.asyncio
async def test_trueforge_protocol_verifies_and_creates_pending_proposal(
    recovery_context,
    monkeypatch,
) -> None:
    factory, incident = recovery_context
    settings = detector_settings()
    monkeypatch.setattr(
        server,
        "tools",
        IncidentInvestigationTools(settings, factory),
    )
    monkeypatch.setattr(
        server,
        "recovery_tools",
        RecoveryAgentTools(settings, factory),
    )

    async with Client(server.mcp, raise_exceptions=True) as client:
        verification = await client.call_tool(
            "verify_incident_evidence",
            {"incident_id": str(incident.id)},
        )
        proposal = await client.call_tool(
            "create_bounded_recovery_proposal",
            {
                "incident_id": str(incident.id),
                "action_type": "create_payment_link",
                "rationale": "Offer approval-gated retries for verified timeout failures.",
                "expires_in_minutes": 30,
            },
        )
        timeline = await client.call_tool(
            "get_incident_audit_timeline",
            {"incident_id": str(incident.id)},
        )

    assert verification.is_error is False
    assert verification.structured_content is not None
    assert verification.structured_content["verified"] is True
    assert verification.structured_content["native_trueforge_sandbox_used"] is False
    assert proposal.is_error is False
    assert proposal.structured_content is not None
    assert proposal.structured_content["status"] == "pending_approval"
    assert proposal.structured_content["execution_performed"] is False
    assert timeline.is_error is False
    assert timeline.structured_content is not None
    assert "plan_proposed" in {
        event["event_type"] for event in timeline.structured_content["events"]
    }