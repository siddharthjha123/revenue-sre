"""Standalone MCP server exposing Revenue SRE investigation tools."""

from typing import Annotated, Literal
from uuid import UUID

from mcp.server import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field

from ..config import get_settings
from ..schemas.recovery import RecoveryActionType, RecoveryProposalResponse
from .incident_tools import IncidentInvestigationTools, IncidentNotFoundError
from .recovery_tools import (
    EvidenceVerificationError,
    NoRecoverablePaymentsError,
    RecoveryAgentTools,
    RecoveryNotFoundError,
)
from .schemas import (
    IncidentAuditTimeline,
    IncidentInvestigation,
    IncidentVerification,
    OpenIncidentList,
)

settings = get_settings()
tools = IncidentInvestigationTools(settings)
recovery_tools = RecoveryAgentTools(settings)

mcp = MCPServer(
    "revenue-sre-investigation",
    instructions=(
        "Investigate persistent payment incidents, verify their evidence, and "
        "prepare bounded policy-reviewed proposals. Proposal creation never calls "
        "Razorpay and remains blocked pending merchant approval. Never claim that "
        "a recovery action or customer contact occurred."
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


@mcp.tool(
    title="Verify payment incident evidence",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
async def verify_incident_evidence(
    incident_id: Annotated[
        UUID,
        Field(description="Incident UUID returned by list_open_incidents."),
    ],
) -> IncidentVerification:
    """Recalculate incident metrics using the explicit non-Daytona fallback."""

    try:
        return await tools.verify_incident_evidence(incident_id)
    except IncidentNotFoundError as error:
        raise ToolError(str(error)) from error


@mcp.tool(
    title="Create bounded recovery proposal",
    annotations=ToolAnnotations(
        read_only_hint=False,
        destructive_hint=False,
        idempotent_hint=True,
        open_world_hint=False,
    ),
)
async def create_bounded_recovery_proposal(
    incident_id: Annotated[
        UUID,
        Field(description="Verified incident UUID."),
    ],
    action_type: Annotated[
        Literal[
            "allow_customer_retry",
            "create_payment_link",
            "engineering_escalation",
            "manual_review",
        ],
        Field(
            description=(
                "Proposed action only. The server derives payment IDs and amounts, "
                "and no action executes here."
            )
        ),
    ],
    rationale: Annotated[
        str,
        Field(min_length=1, max_length=1000, description="Evidence-backed rationale."),
    ],
    expires_in_minutes: Annotated[
        int,
        Field(ge=5, le=60, description="Merchant review window."),
    ] = 30,
) -> RecoveryProposalResponse:
    """Persist a policy-reviewed proposal that remains pending merchant approval."""

    try:
        return await recovery_tools.create_bounded_proposal(
            incident_id=incident_id,
            action_type=RecoveryActionType(action_type),
            rationale=rationale,
            expires_in_minutes=expires_in_minutes,
        )
    except (
        EvidenceVerificationError,
        IncidentNotFoundError,
        NoRecoverablePaymentsError,
        RecoveryNotFoundError,
    ) as error:
        raise ToolError(str(error)) from error


@mcp.tool(
    title="Get recovery proposal status",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
async def get_recovery_proposal(
    proposal_id: Annotated[
        UUID,
        Field(description="Proposal UUID returned by create_bounded_recovery_proposal."),
    ],
) -> RecoveryProposalResponse:
    """Return exact proposal actions, policy result, and approval status."""

    try:
        return await recovery_tools.get_proposal(proposal_id)
    except RecoveryNotFoundError as error:
        raise ToolError(str(error)) from error


@mcp.tool(
    title="Get incident audit timeline",
    annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False),
)
async def get_incident_audit_timeline(
    incident_id: Annotated[
        UUID,
        Field(description="Owned incident UUID."),
    ],
) -> IncidentAuditTimeline:
    """Return the filtered append-only incident, policy, and approval timeline."""

    try:
        return await tools.get_incident_audit_timeline(incident_id)
    except IncidentNotFoundError as error:
        raise ToolError(str(error)) from error


transport_security = TransportSecuritySettings(
    allowed_hosts=settings.mcp_allowed_hosts,
    allowed_origins=settings.mcp_allowed_origins,
)
app = mcp.streamable_http_app(transport_security=transport_security)