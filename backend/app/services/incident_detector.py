"""Deterministic payment failure-spike detection and evidence persistence."""

import hashlib
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database.models.payment_event_fact import PaymentEventFact
from ..database.models.recovery import AuditRecord
from ..database.repositories.incident_repository import IncidentMetrics, IncidentRepository
from ..database.repositories.payment_fact_repository import PaymentFactRepository
from ..schemas.audit import AuditActorType, AuditEventType
from ..schemas.incident import IncidentType
from ..schemas.payment import PaymentStatus

DETECTOR_VERSION = "failure-spike-v1"
_STATUS_RANK = {
    PaymentStatus.CREATED: 0,
    PaymentStatus.FAILED: 1,
    PaymentStatus.AUTHORIZED: 2,
    PaymentStatus.CAPTURED: 3,
    PaymentStatus.REFUNDED: 4,
}


@dataclass(frozen=True, slots=True)
class DetectionResult:
    """Small result returned to the worker pipeline."""

    incident_ids: tuple[UUID, ...]
    created_incident_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class FailureSegment:
    currency: str
    method: str
    bank: str | None
    error_reason: str


class IncidentDetector:
    """Compare current and baseline windows using explainable thresholds."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

    async def detect(
        self,
        session: AsyncSession,
        *,
        merchant_id: UUID,
        correlation_id: UUID,
    ) -> DetectionResult:
        facts_repository = PaymentFactRepository(session)
        anchor = await facts_repository.latest_event_at(merchant_id)
        if anchor is None:
            return DetectionResult((), ())
        anchor = _as_utc(anchor)
        current_start = anchor - timedelta(minutes=self._settings.incident_current_window_minutes)
        baseline_start = current_start - timedelta(
            minutes=self._settings.incident_baseline_window_minutes
        )
        facts = list(
            await facts_repository.list_window(
                merchant_id,
                start_at=baseline_start,
                end_at=anchor,
            )
        )
        baseline = _collapse_payments(
            fact for fact in facts if _as_utc(fact.provider_event_at) < current_start
        )
        current = _collapse_payments(
            fact for fact in facts if _as_utc(fact.provider_event_at) >= current_start
        )

        failure_groups: dict[FailureSegment, list[PaymentEventFact]] = defaultdict(list)
        for fact in current.values():
            if fact.status == PaymentStatus.FAILED:
                failure_groups[
                    FailureSegment(
                        currency=fact.currency,
                        method=fact.method.value,
                        bank=fact.bank,
                        error_reason=fact.error_reason or "unknown",
                    )
                ].append(fact)

        repository = IncidentRepository(session)
        incident_ids: list[UUID] = []
        created_ids: list[UUID] = []
        for segment, failed_facts in failure_groups.items():
            current_attempts = _segment_attempts(current, segment)
            baseline_attempts = _segment_attempts(baseline, segment)
            baseline_failures = [
                fact
                for fact in baseline_attempts
                if fact.status == PaymentStatus.FAILED
                and (fact.error_reason or "unknown") == segment.error_reason
            ]
            current_count = len(current_attempts)
            failure_count = len(failed_facts)
            baseline_count = len(baseline_attempts)
            current_rate = failure_count / current_count if current_count else 0.0
            baseline_rate = len(baseline_failures) / baseline_count if baseline_count else 0.0
            if not self._threshold_exceeded(
                attempts=current_count,
                failures=failure_count,
                current_rate=current_rate,
                baseline_rate=baseline_rate,
            ):
                continue

            money_at_risk = sum(fact.amount_subunits for fact in failed_facts)
            fingerprint = _fingerprint(merchant_id, segment)
            confidence = min(
                0.99,
                0.55
                + min(max(current_rate - baseline_rate, 0.0), 0.30)
                + min(failure_count / 100, 0.14),
            )
            incident, created = await repository.upsert_detected(
                merchant_id,
                IncidentMetrics(
                    fingerprint=fingerprint,
                    incident_type=IncidentType.PAYMENT_FAILURE_SPIKE,
                    currency=segment.currency,
                    method=segment.method,
                    bank=segment.bank,
                    error_reason=segment.error_reason,
                    detector_version=DETECTOR_VERSION,
                    baseline_window_start=baseline_start,
                    current_window_start=current_start,
                    current_window_end=anchor,
                    baseline_attempt_count=baseline_count,
                    baseline_failure_count=len(baseline_failures),
                    current_attempt_count=current_count,
                    current_failure_count=failure_count,
                    baseline_failure_rate=baseline_rate,
                    current_failure_rate=current_rate,
                    revenue_at_risk_subunits=money_at_risk,
                    confidence=confidence,
                ),
            )
            for fact in failed_facts:
                await repository.add_fact_evidence_once(incident, fact)
            metric_details = {
                "baseline_window_start": baseline_start.isoformat(),
                "current_window_start": current_start.isoformat(),
                "current_window_end": anchor.isoformat(),
                "baseline_attempt_count": baseline_count,
                "baseline_failure_count": len(baseline_failures),
                "current_attempt_count": current_count,
                "current_failure_count": failure_count,
                "baseline_failure_rate": baseline_rate,
                "current_failure_rate": current_rate,
                "failure_rate_increase": current_rate - baseline_rate,
                "revenue_at_risk_subunits": money_at_risk,
                "thresholds": {
                    "minimum_attempts": self._settings.incident_minimum_attempts,
                    "minimum_failures": self._settings.incident_minimum_failures,
                    "minimum_failure_rate": self._settings.incident_minimum_failure_rate,
                    "minimum_rate_increase": self._settings.incident_minimum_rate_increase,
                    "baseline_multiplier": self._settings.incident_baseline_multiplier,
                },
            }
            snapshot_hash = hashlib.sha256(
                json.dumps(metric_details, sort_keys=True).encode()
            ).hexdigest()
            await repository.add_metric_evidence_once(
                incident,
                evidence_key=f"metric:{snapshot_hash}",
                details=metric_details,
            )
            if created:
                session.add(
                    AuditRecord(
                        merchant_id=merchant_id,
                        correlation_id=correlation_id,
                        incident_id=incident.id,
                        event_type=AuditEventType.INCIDENT_CREATED,
                        actor_type=AuditActorType.SYSTEM,
                        actor_id=DETECTOR_VERSION,
                        details={"fingerprint": fingerprint, **metric_details},
                        occurred_at=datetime.now(UTC),
                    )
                )
                created_ids.append(incident.id)
            incident_ids.append(incident.id)
        await session.flush()
        return DetectionResult(tuple(incident_ids), tuple(created_ids))

    def _threshold_exceeded(
        self,
        *,
        attempts: int,
        failures: int,
        current_rate: float,
        baseline_rate: float,
    ) -> bool:
        if attempts < self._settings.incident_minimum_attempts:
            return False
        if failures < self._settings.incident_minimum_failures:
            return False
        if current_rate < self._settings.incident_minimum_failure_rate:
            return False
        if current_rate - baseline_rate < self._settings.incident_minimum_rate_increase:
            return False
        if baseline_rate > 0 and current_rate < (
            baseline_rate * self._settings.incident_baseline_multiplier
        ):
            return False
        return True


def _collapse_payments(facts) -> dict[str, PaymentEventFact]:
    """Keep the most advanced observed state for each payment.

    Status rank prevents a late, older failure delivery from making a captured
    payment look unpaid in incident calculations.
    """

    result: dict[str, PaymentEventFact] = {}
    for fact in facts:
        current = result.get(fact.payment_id)
        if current is None or _STATUS_RANK[fact.status] > _STATUS_RANK[current.status]:
            result[fact.payment_id] = fact
        elif fact.status == current.status and _as_utc(fact.provider_event_at) > _as_utc(
            current.provider_event_at
        ):
            result[fact.payment_id] = fact
    return result


def _segment_attempts(
    payments: dict[str, PaymentEventFact], segment: FailureSegment
) -> list[PaymentEventFact]:
    return [
        fact
        for fact in payments.values()
        if fact.currency == segment.currency
        and fact.method.value == segment.method
        and fact.bank == segment.bank
    ]


def _fingerprint(merchant_id: UUID, segment: FailureSegment) -> str:
    value = {
        "merchant_id": str(merchant_id),
        "detector": DETECTOR_VERSION,
        "currency": segment.currency,
        "method": segment.method,
        "bank": segment.bank,
        "error_reason": segment.error_reason,
    }
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
