"""HTTP contract tests for tenant-scoped incidents and merchant decisions."""

from collections.abc import AsyncGenerator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.config import get_settings
from backend.app.database.base import Base, get_db_session
from backend.app.main import create_app
from backend.app.mcp.recovery_tools import RecoveryAgentTools
from backend.app.schemas.recovery import RecoveryProposalResponse
from backend.tests.incident_test_support import (
    MERCHANT_A,
    MERCHANT_B,
    detector_settings,
    seed_failure_spike,
)


@pytest_asyncio.fixture
async def api_context():
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = detector_settings()
    async with factory() as session:
        async with session.begin():
            incident = await seed_failure_spike(session, settings=settings)

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    application = create_app()
    application.dependency_overrides[get_db_session] = override_session
    application.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=application)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, incident.id
    application.dependency_overrides.clear()
    await engine.dispose()


@pytest.mark.asyncio
async def test_incident_proposal_approval_and_audit_endpoints(api_context) -> None:
    client, incident_id = api_context
    headers = {"X-Merchant-Id": str(MERCHANT_A)}

    incident_response = await client.get(f"/incidents/{incident_id}", headers=headers)
    assert incident_response.status_code == 200
    assert incident_response.json()["current_failure_count"] == 3
    assert len(incident_response.json()["evidence"]) == 4

    proposal_response = await client.post(
        f"/incidents/{incident_id}/proposals",
        headers=headers,
        json={
            "actions": [
                {
                    "payment_id": "pay_CURF0",
                    "action_type": "create_payment_link",
                    "amount_subunits": 10000,
                    "rationale": "Offer one approved retry path.",
                }
            ],
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            "created_by": "trueforge-agent",
        },
    )
    assert proposal_response.status_code == 201
    proposal = proposal_response.json()
    assert proposal["policy_allowed"] is True
    assert proposal["status"] == "pending_approval"
    assert len(proposal["evidence_ids"]) == 4

    get_proposal_response = await client.get(
        f"/proposals/{proposal['proposal_id']}",
        headers=headers,
    )
    assert get_proposal_response.status_code == 200
    assert get_proposal_response.json()["content_hash"] == proposal["content_hash"]

    approval_response = await client.post(
        f"/proposals/{proposal['proposal_id']}/approve",
        headers=headers,
        json={
            "decided_by": "merchant-owner",
            "reason": "Approved for a bounded test-mode recovery.",
        },
    )
    assert approval_response.status_code == 201
    assert approval_response.json()["decision"] == "approved"
    assert approval_response.json()["reason"] == "Approved for a bounded test-mode recovery."
    assert approval_response.json()["plan_hash"] == proposal["content_hash"]

    audit_response = await client.get(f"/incidents/{incident_id}/audit", headers=headers)
    assert audit_response.status_code == 200
    event_types = {item["event_type"] for item in audit_response.json()}
    assert {"incident_created", "plan_proposed", "plan_approved"}.issubset(event_types)

    merchant_audit = await client.get("/audit?limit=500", headers=headers)
    assert merchant_audit.status_code == 200
    merchant_events = merchant_audit.json()
    assert any(
        item["event_type"] == "plan_approved"
        and item["plan_id"] == proposal["proposal_id"]
        and item["actor_id"] == "merchant-owner"
        for item in merchant_events
    )

    other_merchant_audit = await client.get(
        "/audit?limit=500",
        headers={"X-Merchant-Id": str(MERCHANT_B)},
    )
    assert other_merchant_audit.status_code == 403


@pytest.mark.asyncio
async def test_incident_endpoint_rejects_another_merchant(api_context) -> None:
    client, incident_id = api_context

    response = await client.get(
        f"/incidents/{incident_id}",
        headers={"X-Merchant-Id": str(MERCHANT_B)},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
async def test_merchant_can_reject_proposal_once_with_immutable_reason(api_context) -> None:
    client, incident_id = api_context
    headers = {"X-Merchant-Id": str(MERCHANT_A)}
    proposal_response = await client.post(
        f"/incidents/{incident_id}/proposals",
        headers=headers,
        json={
            "actions": [
                {
                    "payment_id": "pay_CURF0",
                    "action_type": "allow_customer_retry",
                    "amount_subunits": 10000,
                    "rationale": "Offer one bounded retry.",
                }
            ],
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            "created_by": "trueforge-agent",
        },
    )
    proposal_id = proposal_response.json()["proposal_id"]

    rejection = await client.post(
        f"/proposals/{proposal_id}/reject",
        headers=headers,
        json={
            "decided_by": "merchant-owner",
            "reason": "Do not contact customers during the provider incident.",
        },
    )
    replay = await client.post(
        f"/proposals/{proposal_id}/reject",
        headers=headers,
        json={
            "decided_by": "merchant-owner",
            "reason": "Do not contact customers during the provider incident.",
        },
    )
    conflicting_approval = await client.post(
        f"/proposals/{proposal_id}/approve",
        headers=headers,
        json={"decided_by": "merchant-owner"},
    )

    assert rejection.status_code == 201
    assert rejection.json()["decision"] == "rejected"
    assert rejection.json()["reason"] == ("Do not contact customers during the provider incident.")
    assert replay.status_code == 201
    assert replay.json()["approval_id"] == rejection.json()["approval_id"]
    assert conflicting_approval.status_code == 409


@pytest.mark.asyncio
async def test_active_incident_proposal_is_null_before_agent_creation(api_context) -> None:
    client, incident_id = api_context

    response = await client.get(
        f"/incidents/{incident_id}/proposal",
        headers={"X-Merchant-Id": str(MERCHANT_A)},
    )

    assert response.status_code == 200
    assert response.json() is None


@pytest.mark.asyncio
async def test_bounded_proposal_command_accepts_no_financial_scope(
    api_context,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, incident_id = api_context
    headers = {"X-Merchant-Id": str(MERCHANT_A)}
    persisted = await client.post(
        f"/incidents/{incident_id}/proposals",
        headers=headers,
        json={
            "actions": [
                {
                    "payment_id": "pay_CURF0",
                    "action_type": "create_payment_link",
                    "amount_subunits": 10000,
                    "rationale": "Offer one approved retry path.",
                }
            ],
            "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
            "created_by": "trueforge-agent",
        },
    )
    proposal = RecoveryProposalResponse.model_validate(persisted.json())

    async def return_policy_derived_proposal(*args, **kwargs):
        return proposal

    monkeypatch.setattr(
        RecoveryAgentTools,
        "create_bounded_proposal",
        return_policy_derived_proposal,
    )

    response = await client.post(
        f"/incidents/{incident_id}/bounded-proposal",
        headers=headers,
        json={
            "action_type": "create_payment_link",
            "rationale": "Offer one approval-gated retry path.",
            "expires_in_minutes": 30,
        },
    )

    assert response.status_code == 200
    assert response.json()["proposal_id"] == str(proposal.proposal_id)
    assert response.json()["execution_performed"] is False


@pytest.mark.asyncio
async def test_incident_commander_chat_is_evidence_grounded_and_read_only(api_context) -> None:
    client, incident_id = api_context
    headers = {"X-Merchant-Id": str(MERCHANT_A)}

    response = await client.post(
        f"/incidents/{incident_id}/commander/chat",
        headers=headers,
        json={"message": "How was revenue at risk calculated?"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert "₹600" in payload["answer"]
    assert payload["evidence_count"] == 4
    assert payload["evidence_verified"] is True
    assert "cannot execute" in payload["safety_notice"].lower()

    proposal_response = await client.get(
        f"/incidents/{incident_id}/proposal",
        headers=headers,
    )
    assert proposal_response.json() is None


@pytest.mark.asyncio
async def test_incident_commander_chat_handles_greetings_without_repeating_summary(
    api_context,
) -> None:
    client, incident_id = api_context

    response = await client.post(
        f"/incidents/{incident_id}/commander/chat",
        headers={"X-Merchant-Id": str(MERCHANT_A)},
        json={"message": "hii"},
    )

    assert response.status_code == 200
    answer = response.json()["answer"]
    assert answer.startswith("Hi!")
    assert "HDFC UPI incident" in answer
    assert "Ask me about the evidence" not in answer


@pytest.mark.asyncio
async def test_incident_commander_chat_preserves_merchant_isolation(api_context) -> None:
    client, incident_id = api_context

    response = await client.post(
        f"/incidents/{incident_id}/commander/chat",
        headers={"X-Merchant-Id": str(MERCHANT_B)},
        json={"message": "Summarize this incident."},
    )

    assert response.status_code == 403
