"""Tenant-scoped read services behind the MCP tool boundary."""

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings, get_settings
from ..database.base import AsyncSessionFactory
from ..database.models.incident import Incident
from ..database.repositories.incident_repository import IncidentRepository
from .schemas import EvidenceItem, IncidentInvestigation, IncidentSummary, OpenIncidentList

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
