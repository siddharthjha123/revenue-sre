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
        json={"decided_by": "merchant-owner"},
    )
    assert approval_response.status_code == 201
    assert approval_response.json()["decision"] == "approved"
    assert approval_response.json()["plan_hash"] == proposal["content_hash"]

    audit_response = await client.get(f"/incidents/{incident_id}/audit", headers=headers)
    assert audit_response.status_code == 200
    event_types = {item["event_type"] for item in audit_response.json()}
    assert {"incident_created", "plan_proposed", "plan_approved"}.issubset(event_types)


@pytest.mark.asyncio
async def test_incident_endpoint_rejects_another_merchant(api_context) -> None:
    client, incident_id = api_context

    response = await client.get(
        f"/incidents/{incident_id}",
        headers={"X-Merchant-Id": str(MERCHANT_B)},
    )

    assert response.status_code == 403