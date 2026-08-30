"""Tenant-scoped read services behind the MCP tool boundary."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings, get_settings
from ..database.base import AsyncSessionFactory
from ..database.models.incident import Incident
from ..database.repositories.incident_repository import IncidentRepository
from ..database.repositories.recovery_repository import RecoveryRepository
from .schemas import (
    AgentAuditEvent,
    EvidenceItem,
    IncidentAuditTimeline,
    IncidentInvestigation,
    IncidentSummary,
    IncidentVerification,
    OpenIncidentList,
    VerificationCheck,
)

# Evidence details are persisted as JSON. Allowlisting prevents a future writer
# from accidentally exposing customer PII through this agent-facing surface.
_SAFE_EVIDENCE_DETAIL_KEYS = frozenset(
    {
        "payment_id",
        "event_type",
        "amount_subunits",
        "currency",
        "method",
        "bank",
        "error_source",
        "error_reason",
        "provider_event_at",
        "baseline_window_start",
        "current_window_start",
        "current_window_end",
        "baseline_attempt_count",
        "baseline_failure_count",
        "current_attempt_count",
        "current_failure_count",
        "baseline_failure_rate",
        "current_failure_rate",
        "failure_rate_increase",
        "revenue_at_risk_subunits",
        "thresholds",
    }
)
_SAFE_AUDIT_DETAIL_KEYS = frozenset(
    {
        "allowed",
        "evidence_ids",
        "fingerprint",
        "plan_hash",
        "reasons",
        "total_amount_subunits",
    }
)


class MCPConfigurationError(RuntimeError):
    """Raised when the server has no trusted merchant identity."""


class IncidentNotFoundError(LookupError):
    """Raised without revealing whether an incident belongs to another merchant."""


class IncidentInvestigationTools:
    """Read-only incident queries with server-controlled tenant identity."""

    def __init__(
        self,
        settings: Settings | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory or AsyncSessionFactory

    async def list_open_incidents(self, *, limit: int = 20) -> OpenIncidentList:
        """List highest-risk open incidents for the configured merchant."""

        merchant_id = self._merchant_id()
        async with self._session_factory() as session:
            records = await IncidentRepository(session).list_actionable(
                merchant_id,
                limit=limit,
            )
            return OpenIncidentList(incidents=[_summary(item) for item in records])

    async def get_incident_evidence(self, incident_id: UUID) -> IncidentInvestigation:
        """Get exact persisted evidence for one owned incident."""

        merchant_id = self._merchant_id()
        async with self._session_factory() as session:
            repository = IncidentRepository(session)
            incident = await repository.get(merchant_id, incident_id)
            if incident is None:
                raise IncidentNotFoundError("Incident was not found")
            evidence = await repository.list_evidence(merchant_id, incident_id)
            return IncidentInvestigation(
                incident=_summary(incident),
                evidence=[
                    EvidenceItem(
                        evidence_id=item.id,
                        kind=item.kind,
                        summary=item.summary,
                        source_reference=item.source_reference,
                        details={
                            key: value
                            for key, value in item.details.items()
                            if key in _SAFE_EVIDENCE_DETAIL_KEYS
                        },
                        created_at=_as_utc(item.created_at),
                    )
                    for item in evidence
                ],
            )

    async def verify_incident_evidence(self, incident_id: UUID) -> IncidentVerification:
        """Recalculate persisted metrics when native TrueForge sandbox is unavailable."""

        investigation = await self.get_incident_evidence(incident_id)
        incident = investigation.incident
        facts = [item for item in investigation.evidence if item.kind.value == "razorpay_fact"]
        metrics = [item for item in investigation.evidence if item.kind.value == "sandbox_metric"]
        payment_ids = {
            str(item.details["payment_id"]) for item in facts if item.details.get("payment_id")
        }
        evidence_risk = sum(
            value
            for item in facts
            if isinstance((value := item.details.get("amount_subunits")), int)
        )
        baseline_rate = (
            incident.baseline_failure_count / incident.baseline_attempt_count
            if incident.baseline_attempt_count
            else 0.0
        )
        current_rate = (
            incident.current_failure_count / incident.current_attempt_count
            if incident.current_attempt_count
            else 0.0
        )
        checks = [
            VerificationCheck(
                name="baseline_failure_rate",
                passed=abs(baseline_rate - incident.baseline_failure_rate) <= 1e-9,
            ),
            VerificationCheck(
                name="current_failure_rate",
                passed=abs(current_rate - incident.current_failure_rate) <= 1e-9,
            ),
            VerificationCheck(
                name="failed_evidence_count",
                passed=len(payment_ids) == incident.current_failure_count,
            ),
            VerificationCheck(
                name="money_at_risk",
                passed=evidence_risk == incident.revenue_at_risk_subunits,
            ),
            VerificationCheck(name="metric_snapshot", passed=len(metrics) == 1),
        ]
        if metrics:
            details = metrics[0].details
            checks.extend(
                [
                    VerificationCheck(
                        name="metric_current_attempt_count",
                        passed=details.get("current_attempt_count")
                        == incident.current_attempt_count,
                    ),
                    VerificationCheck(
                        name="metric_current_failure_count",
                        passed=details.get("current_failure_count")
                        == incident.current_failure_count,
                    ),
                    VerificationCheck(
                        name="metric_money_at_risk",
                        passed=details.get("revenue_at_risk_subunits")
                        == incident.revenue_at_risk_subunits,
                    ),
                ]
            )
        return IncidentVerification(
            incident_id=incident.incident_id,
            verified=all(check.passed for check in checks),
            baseline_failure_rate=baseline_rate,
            current_failure_rate=current_rate,
            failure_rate_increase=current_rate - baseline_rate,
            failed_payment_count=len(payment_ids),
            revenue_at_risk_subunits=evidence_risk,
            evidence_ids=sorted(item.evidence_id for item in investigation.evidence),
            checks=checks,
            limitations=[
                "This fallback is executed by Revenue SRE MCP, not Daytona sandbox.",
                "Historical snapshots are retained but excluded from current-window totals.",
                "Evidence consistency does not prove a provider's internal root cause.",
                "Verification does not authorize or execute recovery.",
            ],
        )

    async def get_incident_audit_timeline(
        self,
        incident_id: UUID,
    ) -> IncidentAuditTimeline:
        """Return a filtered append-only timeline for one owned incident."""

        merchant_id = self._merchant_id()
        async with self._session_factory() as session:
            if await IncidentRepository(session).get(merchant_id, incident_id) is None:
                raise IncidentNotFoundError("Incident was not found")
            records = await RecoveryRepository(session).list_audit(
                merchant_id,
                incident_id=incident_id,
            )
            return IncidentAuditTimeline(
                incident_id=incident_id,
                events=[
                    AgentAuditEvent(
                        audit_id=record.id,
                        incident_id=incident_id,
                        proposal_id=record.proposal_id,
                        event_type=record.event_type,
                        actor_type=record.actor_type,
                        # Actor identifiers can be emails in merchant-facing APIs.
                        # The agent needs the role, not a potentially identifying value.
                        actor_id=f"{record.actor_type.value}-actor",
                        occurred_at=_as_utc(record.occurred_at),
                        details={
                            key: value
                            for key, value in record.details.items()
                            if key in _SAFE_AUDIT_DETAIL_KEYS
                        },
                    )
                    for record in records
                ],
            )

    def _merchant_id(self) -> UUID:
        merchant_id = self._settings.merchant_id
        if merchant_id is None:
            raise MCPConfigurationError("MCP merchant identity is not configured")
        return merchant_id


def _summary(incident: Incident) -> IncidentSummary:
    return IncidentSummary(
        incident_id=incident.id,
        incident_type=incident.incident_type,
        status=incident.status,
        currency=incident.currency,
        method=incident.method,
        bank=incident.bank,
        error_reason=incident.error_reason,
        baseline_attempt_count=incident.baseline_attempt_count,
        baseline_failure_count=incident.baseline_failure_count,
        current_attempt_count=incident.current_attempt_count,
        current_failure_count=incident.current_failure_count,
        baseline_failure_rate=incident.baseline_failure_rate,
        current_failure_rate=incident.current_failure_rate,
        revenue_at_risk_subunits=incident.revenue_at_risk_subunits,
        confidence=incident.confidence,
        opened_at=_as_utc(incident.opened_at),
        last_detected_at=_as_utc(incident.last_detected_at),
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
