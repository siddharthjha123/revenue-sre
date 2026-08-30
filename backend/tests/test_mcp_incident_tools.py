"""Proofs for the tenant-scoped, read-only incident MCP surface."""

from collections.abc import AsyncGenerator
from uuid import uuid4

import pytest
import pytest_asyncio
from mcp import Client
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from backend.app.database.base import Base
from backend.app.database.models.incident import Incident, IncidentEvidenceRecord
from backend.app.mcp import server
from backend.app.mcp.incident_tools import (
    IncidentInvestigationTools,
    IncidentNotFoundError,
    MCPConfigurationError,
)
from backend.app.schemas.incident import IncidentStatus
from backend.tests.incident_test_support import (
    MERCHANT_A,
    MERCHANT_B,
    detector_settings,
    seed_failure_spike,
)


@pytest_asyncio.fixture
async def mcp_context() -> AsyncGenerator[tuple[async_sessionmaker, Incident], None]:
    engine = create_async_engine("sqlite+aiosqlite://", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        async with session.begin():
            owned_incident = await seed_failure_spike(
                session,
                settings=detector_settings(),
                merchant_id=MERCHANT_A,
            )
            await seed_failure_spike(
                session,
                settings=detector_settings(),
                merchant_id=MERCHANT_B,
            )
    yield factory, owned_incident
    await engine.dispose()


@pytest.mark.asyncio
async def test_list_open_incidents_is_tenant_scoped_and_risk_ordered(mcp_context) -> None:
    factory, owned_incident = mcp_context
    tools = IncidentInvestigationTools(detector_settings(), factory)

    result = await tools.list_open_incidents(limit=20)

    assert [item.incident_id for item in result.incidents] == [owned_incident.id]
    assert result.incidents[0].current_failure_count == 3
    assert result.incidents[0].revenue_at_risk_subunits == 60_000


@pytest.mark.asyncio
async def test_list_open_incidents_excludes_closed_incidents(mcp_context) -> None:
    factory, owned_incident = mcp_context
    async with factory() as session:
        async with session.begin():
            record = await session.get(Incident, owned_incident.id)
            assert record is not None
            record.status = IncidentStatus.RESOLVED

    result = await IncidentInvestigationTools(
        detector_settings(),
        factory,
    ).list_open_incidents()

    assert result.incidents == []


@pytest.mark.asyncio
async def test_get_incident_evidence_preserves_facts_and_filters_pii(mcp_context) -> None:
    factory, owned_incident = mcp_context
    async with factory() as session:
        async with session.begin():
            evidence = await session.scalar(
                select(IncidentEvidenceRecord).where(
                    IncidentEvidenceRecord.incident_id == owned_incident.id,
                    IncidentEvidenceRecord.payment_event_fact_id.is_not(None),
                )
            )
            assert evidence is not None
            evidence.details = {
                **evidence.details,
                "email": "must-not-leak@example.com",
                "contact": "+919999999999",
                "authorization": "must-not-leak",
            }

    result = await IncidentInvestigationTools(
        detector_settings(),
        factory,
    ).get_incident_evidence(owned_incident.id)
    serialized = result.model_dump_json()

    assert len(result.evidence) == 4
    assert "pay_CURF0" in serialized
    assert "must-not-leak" not in serialized
    assert "email" not in serialized
    assert "contact" not in serialized


@pytest.mark.asyncio
async def test_get_incident_evidence_hides_cross_tenant_existence(mcp_context) -> None:
    factory, _ = mcp_context
    async with factory() as session:
        other_incident = await session.scalar(
            select(Incident).where(Incident.merchant_id == MERCHANT_B)
        )
        assert other_incident is not None

    with pytest.raises(IncidentNotFoundError, match="Incident was not found"):
        await IncidentInvestigationTools(
            detector_settings(),
            factory,
        ).get_incident_evidence(other_incident.id)


@pytest.mark.asyncio
async def test_tools_require_trusted_server_merchant(mcp_context) -> None:
    factory, _ = mcp_context
    tools = IncidentInvestigationTools(detector_settings(merchant_id=None), factory)

    with pytest.raises(MCPConfigurationError, match="merchant identity"):
        await tools.list_open_incidents()


@pytest.mark.asyncio
async def test_mcp_protocol_exposes_two_read_only_tools_without_writes(
    mcp_context,
    monkeypatch,
) -> None:
    factory, owned_incident = mcp_context
    monkeypatch.setattr(
        server,
        "tools",
        IncidentInvestigationTools(detector_settings(), factory),
    )
    async with factory() as session:
        incident_count_before = await session.scalar(select(func.count()).select_from(Incident))
        evidence_count_before = await session.scalar(
            select(func.count()).select_from(IncidentEvidenceRecord)
        )

    async with Client(server.mcp, raise_exceptions=True) as client:
        listed_tools = await client.list_tools()
        by_name = {tool.name: tool for tool in listed_tools.tools}
        list_result = await client.call_tool("list_open_incidents", {"limit": 5})
        evidence_result = await client.call_tool(
            "get_incident_evidence",
            {"incident_id": str(owned_incident.id)},
        )

    assert set(by_name) == {"list_open_incidents", "get_incident_evidence"}
    assert all(tool.annotations and tool.annotations.read_only_hint for tool in by_name.values())
    assert all(
        tool.annotations and not tool.annotations.open_world_hint
        for tool in by_name.values()
    )
    assert list_result.is_error is False
    assert evidence_result.is_error is False
    assert list_result.structured_content is not None
    assert evidence_result.structured_content is not None
    assert str(owned_incident.id) in evidence_result.structured_content["incident"]["incident_id"]

    async with factory() as session:
        incident_count_after = await session.scalar(
            select(func.count()).select_from(Incident)
        )
        assert incident_count_after == incident_count_before
        assert (
            await session.scalar(select(func.count()).select_from(IncidentEvidenceRecord))
            == evidence_count_before
        )


@pytest.mark.asyncio
async def test_unknown_incident_returns_safe_mcp_tool_error(mcp_context, monkeypatch) -> None:
    factory, _ = mcp_context
    monkeypatch.setattr(
        server,
        "tools",
        IncidentInvestigationTools(detector_settings(), factory),
    )

    async with Client(server.mcp, raise_exceptions=True) as client:
        result = await client.call_tool(
            "get_incident_evidence",
            {"incident_id": str(uuid4())},
        )

    assert result.is_error is True
    assert "Incident was not found" in result.content[0].text
