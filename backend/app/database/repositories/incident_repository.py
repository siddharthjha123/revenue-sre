"""Tenant-safe incident and evidence persistence."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ...schemas.incident import EvidenceKind, IncidentStatus, IncidentType
from ..models.incident import Incident, IncidentEvidenceRecord
from ..models.payment_event_fact import PaymentEventFact


@dataclass(frozen=True, slots=True)
class IncidentMetrics:
    fingerprint: str
    incident_type: IncidentType
    currency: str
    method: str
    bank: str | None
    error_reason: str
    detector_version: str
    baseline_window_start: datetime
    current_window_start: datetime
    current_window_end: datetime
    baseline_attempt_count: int
    baseline_failure_count: int
    current_attempt_count: int
    current_failure_count: int
    baseline_failure_rate: float
    current_failure_rate: float
    revenue_at_risk_subunits: int
    confidence: float


class IncidentRepository:
    """Database operations with mandatory merchant boundaries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def upsert_detected(
        self,
        merchant_id: UUID,
        metrics: IncidentMetrics,
    ) -> tuple[Incident, bool]:
        incident_id = uuid4()
        values = {
            "id": incident_id,
            "merchant_id": merchant_id,
            "fingerprint": metrics.fingerprint,
            "incident_type": metrics.incident_type,
            "status": IncidentStatus.OPEN,
            "currency": metrics.currency,
            "method": metrics.method,
            "bank": metrics.bank,
            "error_reason": metrics.error_reason,
            "detector_version": metrics.detector_version,
            "baseline_window_start": metrics.baseline_window_start,
            "current_window_start": metrics.current_window_start,
            "current_window_end": metrics.current_window_end,
            "baseline_attempt_count": metrics.baseline_attempt_count,
            "baseline_failure_count": metrics.baseline_failure_count,
            "current_attempt_count": metrics.current_attempt_count,
            "current_failure_count": metrics.current_failure_count,
            "baseline_failure_rate": metrics.baseline_failure_rate,
            "current_failure_rate": metrics.current_failure_rate,
            "revenue_at_risk_subunits": metrics.revenue_at_risk_subunits,
            "confidence": metrics.confidence,
            "opened_at": metrics.current_window_end,
            "last_detected_at": metrics.current_window_end,
        }
        dialect = self._session.get_bind().dialect.name
        statement = (
            postgresql_insert(Incident)
            if dialect == "postgresql"
            else sqlite_insert(Incident)
            if dialect == "sqlite"
            else insert(Incident)
        )
        if dialect in {"postgresql", "sqlite"}:
            statement = statement.on_conflict_do_nothing(
                index_elements=[Incident.merchant_id, Incident.fingerprint]
            )
        result = await self._session.execute(statement.values(**values).returning(Incident.id))
        created = result.scalar_one_or_none() is not None
        incident = await self._session.scalar(
            select(Incident)
            .where(
                Incident.merchant_id == merchant_id,
                Incident.fingerprint == metrics.fingerprint,
            )
            .with_for_update()
        )
        if incident is None:
            raise RuntimeError("Incident upsert did not resolve an incident")
        if not created:
            for field_name in (
                "baseline_window_start",
                "current_window_start",
                "current_window_end",
                "baseline_attempt_count",
                "baseline_failure_count",
                "current_attempt_count",
                "current_failure_count",
                "baseline_failure_rate",
                "current_failure_rate",
                "revenue_at_risk_subunits",
                "confidence",
            ):
                setattr(incident, field_name, getattr(metrics, field_name))
            incident.last_detected_at = metrics.current_window_end
            await self._session.flush()
        return incident, created

    async def add_fact_evidence_once(
        self,
        incident: Incident,
        fact: PaymentEventFact,
    ) -> IncidentEvidenceRecord:
        return await self._add_evidence_once(
            incident=incident,
            evidence_key=f"fact:{fact.id}",
            kind=EvidenceKind.RAZORPAY_FACT,
            summary=(
                f"{fact.event_type} for {fact.payment_id} via {fact.method.value} "
                f"with reason {fact.error_reason or 'unknown'}"
            ),
            source_reference=fact.razorpay_event_id,
            details={
                "payment_id": fact.payment_id,
                "event_type": fact.event_type,
                "amount_subunits": fact.amount_subunits,
                "currency": fact.currency,
                "method": fact.method.value,
                "bank": fact.bank,
                "error_source": fact.error_source.value if fact.error_source else None,
                "error_reason": fact.error_reason,
                "provider_event_at": fact.provider_event_at.isoformat(),
            },
            payment_event_fact_id=fact.id,
        )

    async def add_metric_evidence_once(
        self,
        incident: Incident,
        *,
        evidence_key: str,
        details: dict[str, Any],
    ) -> IncidentEvidenceRecord:
        return await self._add_evidence_once(
            incident=incident,
            evidence_key=evidence_key,
            kind=EvidenceKind.SANDBOX_METRIC,
            summary="Deterministic failure-rate threshold was exceeded",
            source_reference=incident.detector_version,
            details=details,
        )

    async def _add_evidence_once(
        self,
        *,
        incident: Incident,
        evidence_key: str,
        kind: EvidenceKind,
        summary: str,
        source_reference: str | None,
        details: dict[str, Any],
        payment_event_fact_id: UUID | None = None,
    ) -> IncidentEvidenceRecord:
        existing = await self._session.scalar(
            select(IncidentEvidenceRecord).where(
                IncidentEvidenceRecord.incident_id == incident.id,
                IncidentEvidenceRecord.evidence_key == evidence_key,
            )
        )
        if existing is not None:
            return existing
        evidence = IncidentEvidenceRecord(
            incident_id=incident.id,
            merchant_id=incident.merchant_id,
            payment_event_fact_id=payment_event_fact_id,
            evidence_key=evidence_key,
            kind=kind,
            summary=summary,
            source_reference=source_reference,
            details=details,
        )
        self._session.add(evidence)
        await self._session.flush()
        return evidence

    async def get(self, merchant_id: UUID, incident_id: UUID) -> Incident | None:
        return await self._session.scalar(
            select(Incident).where(
                Incident.id == incident_id,
                Incident.merchant_id == merchant_id,
            )
        )

    async def list(self, merchant_id: UUID, *, limit: int = 100) -> Sequence[Incident]:
        result = await self._session.scalars(
            select(Incident)
            .where(Incident.merchant_id == merchant_id)
            .order_by(Incident.last_detected_at.desc())
            .limit(min(max(limit, 1), 500))
        )
        return result.all()

    async def list_evidence(
        self, merchant_id: UUID, incident_id: UUID
    ) -> Sequence[IncidentEvidenceRecord]:
        result = await self._session.scalars(
            select(IncidentEvidenceRecord)
            .where(
                IncidentEvidenceRecord.merchant_id == merchant_id,
                IncidentEvidenceRecord.incident_id == incident_id,
            )
            .order_by(IncidentEvidenceRecord.created_at, IncidentEvidenceRecord.id)
        )
        return result.all()
