"""Contracts for isolated failures and grouped revenue incidents."""

from enum import StrEnum
from typing import Any
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .payment import CurrencyCode, RazorpayPaymentId


class IncidentType(StrEnum):
    SINGLE_PAYMENT_FAILURE = "single_payment_failure"
    PAYMENT_FAILURE_SPIKE = "payment_failure_spike"
    PAYMENT_METHOD_DEGRADATION = "payment_method_degradation"
    BANK_DECLINE_SPIKE = "bank_decline_spike"
    MERCHANT_INTEGRATION_REGRESSION = "merchant_integration_regression"
    GATEWAY_DEGRADATION = "gateway_degradation"
    UNKNOWN = "unknown"


class IncidentStatus(StrEnum):
    OPEN = "open"
    INVESTIGATING = "investigating"
    MITIGATED = "mitigated"
    RESOLVED = "resolved"
    FALSE_POSITIVE = "false_positive"


class EvidenceKind(StrEnum):
    RAZORPAY_FACT = "razorpay_fact"
    MERCHANT_FACT = "merchant_fact"
    SANDBOX_METRIC = "sandbox_metric"
    AGENT_HYPOTHESIS = "agent_hypothesis"


class IncidentEvidence(BaseModel):
    """One auditable fact, metric, or explicitly labelled hypothesis."""

    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: EvidenceKind
    summary: str = Field(min_length=1, max_length=1000)
    source_reference: str | None = Field(default=None, max_length=256)


class IncidentBase(BaseModel):
    """Shared incident fields before persistence assigns an incident ID."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    merchant_id: UUID
    incident_type: IncidentType
    payment_ids: list[RazorpayPaymentId] = Field(default_factory=list)
    revenue_at_risk_subunits: int = Field(default=0, ge=0)
    currency: CurrencyCode = "INR"
    baseline_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    current_success_rate: float | None = Field(default=None, ge=0.0, le=1.0)
    probable_cause: str | None = Field(default=None, max_length=1000)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    evidence: list[IncidentEvidence] = Field(default_factory=list)


class IncidentResponse(IncidentBase):
    """Persisted incident returned to the merchant dashboard."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        from_attributes=True,
    )

    incident_id: UUID
    status: IncidentStatus = IncidentStatus.OPEN
    started_at: AwareDatetime
    ended_at: AwareDatetime | None = None


class IncidentEvidenceResponse(BaseModel):
    """Persisted evidence safe to expose to the owning merchant."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    evidence_id: UUID
    kind: EvidenceKind
    summary: str
    source_reference: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)
    created_at: AwareDatetime


class DetectedIncidentResponse(BaseModel):
    """Detailed deterministic incident metrics returned by the dashboard API."""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    incident_id: UUID
    merchant_id: UUID
    incident_type: IncidentType
    status: IncidentStatus
    currency: CurrencyCode
    method: str
    bank: str | None = None
    error_reason: str
    detector_version: str
    baseline_window_start: AwareDatetime
    current_window_start: AwareDatetime
    current_window_end: AwareDatetime
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
    evidence: list[IncidentEvidenceResponse] = Field(default_factory=list)
