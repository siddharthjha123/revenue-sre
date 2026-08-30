"""Standalone MCP server exposing Revenue SRE investigation tools."""

from typing import Annotated
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from ..config import get_settings
from .incident_tools import IncidentInvestigationTools, IncidentNotFoundError
from .schemas import IncidentInvestigation, OpenIncidentList

settings = get_settings()
tools = IncidentInvestigationTools(settings)

mcp = MCPServer(
    "revenue-sre-investigation",
    instructions=(
        "Use these read-only tools to investigate persistent payment incidents. "
        "Treat deterministic metrics and evidence as facts. Do not claim that a "
        "recovery action executed, and stop before any money-related operation."
    ),
)


@mcp.tool(
    title="List open payment incidents",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
async def list_open_incidents(
    limit: Annotated[
        int,
        Field(
            ge=1,
            le=50,
            description="Maximum actionable incidents, ordered by money at risk.",
        ),
    ] = 20,
) -> OpenIncidentList:
    """List open or investigating incidents for the authenticated merchant."""

    return await tools.list_open_incidents(limit=limit)


@mcp.tool(
    title="Get payment incident evidence",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
async def get_incident_evidence(
    incident_id: Annotated[
        UUID,
        Field(description="Incident UUID returned by list_open_incidents."),
    ],
) -> IncidentInvestigation:
    """Return exact deterministic metrics and PII-safe evidence for one incident."""

    try:
        return await tools.get_incident_evidence(incident_id)
    except IncidentNotFoundError as error:
        # This message is intentionally safe for the model. Unexpected errors
        # remain sanitized by the SDK and are only written to server logs.
        raise ToolError(str(error)) from error


transport_security = TransportSecuritySettings(
    allowed_hosts=settings.mcp_allowed_hosts,
    allowed_origins=settings.mcp_allowed_origins,
)
app = mcp.streamable_http_app(transport_security=transport_security)
