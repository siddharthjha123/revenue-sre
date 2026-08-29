"""Persistent incidents and their append-only supporting evidence."""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from ...schemas.incident import EvidenceKind, IncidentStatus, IncidentType
from ..base import Base


class Incident(Base):
    """Current durable representation of one detected failure segment."""

    __tablename__ = "incidents"
    __table_args__ = (
        UniqueConstraint("merchant_id", "fingerprint", name="uq_incidents_merchant_fingerprint"),
        Index("ix_incidents_merchant_status_updated", "merchant_id", "status", "updated_at"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    incident_type: Mapped[IncidentType] = mapped_column(
        Enum(
            IncidentType,
            name="incident_type",
            native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
    )
    status: Mapped[IncidentStatus] = mapped_column(
        Enum(
            IncidentStatus,
            name="incident_status",
            native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
        default=IncidentStatus.OPEN,
        server_default=IncidentStatus.OPEN.value,
    )
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    method: Mapped[str] = mapped_column(String(32), nullable=False)
    bank: Mapped[str | None] = mapped_column(String(64))
    error_reason: Mapped[str] = mapped_column(String(128), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(32), nullable=False)
    baseline_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    current_window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    baseline_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    current_attempt_count: Mapped[int] = mapped_column(Integer, nullable=False)
    current_failure_count: Mapped[int] = mapped_column(Integer, nullable=False)
    baseline_failure_rate: Mapped[float] = mapped_column(Float, nullable=False)
    current_failure_rate: Mapped[float] = mapped_column(Float, nullable=False)
    revenue_at_risk_subunits: Mapped[int] = mapped_column(Integer, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class IncidentEvidenceRecord(Base):
    """Append-only evidence tied to an incident and, when applicable, one fact."""

    __tablename__ = "incident_evidence"
    __table_args__ = (
        UniqueConstraint("incident_id", "evidence_key", name="uq_incident_evidence_key"),
        Index("ix_incident_evidence_merchant_incident", "merchant_id", "incident_id"),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    incident_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("incidents.id", ondelete="CASCADE"), nullable=False
    )
    merchant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False)
    payment_event_fact_id: Mapped[UUID | None] = mapped_column(
        Uuid(as_uuid=True), ForeignKey("payment_event_facts.id", ondelete="RESTRICT")
    )
    evidence_key: Mapped[str] = mapped_column(String(128), nullable=False)
    kind: Mapped[EvidenceKind] = mapped_column(
        Enum(
            EvidenceKind,
            name="evidence_kind",
            native_enum=False,
            values_callable=lambda e: [i.value for i in e],
        ),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(String(1000), nullable=False)
    source_reference: Mapped[str | None] = mapped_column(String(256))
    details: Mapped[dict[str, Any]] = mapped_column(
        JSON().with_variant(JSONB, "postgresql"), nullable=False, default=dict
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
