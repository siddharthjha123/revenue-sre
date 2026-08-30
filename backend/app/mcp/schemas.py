"""Structured, PII-safe contracts returned to the investigation agent."""

from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from ..schemas.audit import AuditActorType, AuditEventType
from ..schemas.incident import EvidenceKind, IncidentStatus, IncidentType
from ..schemas.payment import CurrencyCode


class IncidentSummary(BaseModel):
    """Deterministic incident metrics needed to choose an investigation target."""

    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    incident_type: IncidentType
    status: IncidentStatus
    currency: CurrencyCode
    method: str
    bank: str | None = None
    error_reason: str
    baseline_attempt_count: int = Field(ge=0)
    baseline_failure_count: int = Field(ge=0)
    current_attempt_count: int = Field(ge=0)
    current_failure_count: int = Field(ge=0)
    baseline_failure_rate: float = Field(ge=0, le=1)
    current_failure_rate: float = Field(ge=0, le=1)
    revenue_at_risk_subunits: int = Field(ge=0)
    confidence: float = Field(ge=0, le=1)
    opened_at: AwareDatetime
    last_detected_at: AwareDatetime


class OpenIncidentList(BaseModel):
    """Actionable incidents for the authenticated server-side merchant."""

    model_config = ConfigDict(extra="forbid")

    incidents: list[IncidentSummary]


class EvidenceItem(BaseModel):
    """One persisted evidence record with an allowlisted details payload."""

    model_config = ConfigDict(extra="forbid")

    evidence_id: UUID
    kind: EvidenceKind
    summary: str
    source_reference: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime


class IncidentInvestigation(BaseModel):
    """Full deterministic context for Qwen to explain and plan against."""

    model_config = ConfigDict(extra="forbid")

    incident: IncidentSummary
    evidence: list[EvidenceItem]


class VerificationCheck(BaseModel):
    """One safe equality check produced by the fallback verifier."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool


class IncidentVerification(BaseModel):
    """Truthful non-Daytona verification result for degraded local demos."""

    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    verified: bool
    verification_runtime: str = "revenue_sre_mcp_fallback"
    native_trueforge_sandbox_used: bool = False
    baseline_failure_rate: float = Field(ge=0, le=1)
    current_failure_rate: float = Field(ge=0, le=1)
    failure_rate_increase: float = Field(ge=-1, le=1)
    failed_payment_count: int = Field(ge=0)
    revenue_at_risk_subunits: int = Field(ge=0)
    evidence_ids: list[UUID]
    checks: list[VerificationCheck]
    limitations: list[str]


class AgentAuditEvent(BaseModel):
    """PII-safe material event shown to the investigation agent."""

    model_config = ConfigDict(extra="forbid")

    audit_id: UUID
    incident_id: UUID
    proposal_id: UUID | None = None
    event_type: AuditEventType
    actor_type: AuditActorType
    actor_id: str
    occurred_at: AwareDatetime
    details: dict[str, Any]


class IncidentAuditTimeline(BaseModel):
    """Append-only incident timeline for agent explanation and demo visibility."""

    model_config = ConfigDict(extra="forbid")

    incident_id: UUID
    events: list[AgentAuditEvent]
