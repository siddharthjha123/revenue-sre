"""Merchant-scoped incident, proposal, approval, and audit endpoints."""

from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database.base import get_db_session
from ..database.models.incident import Incident
from ..database.repositories.incident_repository import IncidentRepository
from ..database.repositories.recovery_repository import RecoveryRepository
from ..observability.context import get_correlation_id
from ..schemas.audit import ApprovalDecisionType, AuditEvent
from ..schemas.incident import DetectedIncidentResponse, IncidentEvidenceResponse
from ..schemas.recovery import (
    ProposalDecisionRequest,
    ProposalDecisionResponse,
    RecoveryProposalCreate,
    RecoveryProposalResponse,
)
from ..services.recovery_service import (
    RecoveryConflictError,
    RecoveryNotFoundError,
    RecoveryPolicyRejectedError,
    RecoveryService,
)
from .dependencies import require_merchant

router = APIRouter(tags=["incidents"])


@router.get("/incidents", response_model=list[DetectedIncidentResponse])
async def list_incidents(
    merchant_id: Annotated[UUID, Depends(require_merchant)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[DetectedIncidentResponse]:
    """List deterministic incidents belonging only to the current merchant."""

    incidents = await IncidentRepository(session).list(merchant_id, limit=limit)
    return [_incident_response(incident, []) for incident in incidents]


@router.get("/incidents/{incident_id}", response_model=DetectedIncidentResponse)
async def get_incident(
    incident_id: UUID,
    merchant_id: Annotated[UUID, Depends(require_merchant)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DetectedIncidentResponse:
    """Return exact statistics and evidence for one merchant incident."""

    repository = IncidentRepository(session)
    incident = await repository.get(merchant_id, incident_id)
    if incident is None:
        raise HTTPException(status_code=404, detail="Incident was not found")
    evidence = await repository.list_evidence(merchant_id, incident_id)
    return _incident_response(incident, evidence)


@router.post(
    "/incidents/{incident_id}/proposals",
    response_model=RecoveryProposalResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_recovery_proposal(
    incident_id: UUID,
    request: RecoveryProposalCreate,
    merchant_id: Annotated[UUID, Depends(require_merchant)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RecoveryProposalResponse:
    """Persist a proposal after deterministic policy evaluation.

    This endpoint never calls Razorpay. Even an allowed proposal remains
    blocked in ``pending_approval`` until a separate merchant decision.
    """

    try:
        async with session.begin():
            return await RecoveryService(settings).create_proposal(
                session,
                merchant_id=merchant_id,
                incident_id=incident_id,
                request=request,
                correlation_id=get_correlation_id(),
            )
    except RecoveryNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.get(
    "/proposals/{proposal_id}",
    response_model=RecoveryProposalResponse,
)
async def get_recovery_proposal(
    proposal_id: UUID,
    merchant_id: Annotated[UUID, Depends(require_merchant)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> RecoveryProposalResponse:
    """Return a merchant-owned proposal for review or status polling."""

    try:
        return await RecoveryService(settings).get_proposal(
            session,
            merchant_id=merchant_id,
            proposal_id=proposal_id,
        )
    except RecoveryNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error


@router.post(
    "/proposals/{proposal_id}/approve",
    response_model=ProposalDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def approve_recovery_proposal(
    proposal_id: UUID,
    request: ProposalDecisionRequest,
    merchant_id: Annotated[UUID, Depends(require_merchant)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProposalDecisionResponse:
    """Record immutable authority for the exact proposal hash—without executing it."""

    return await _decide(
        session,
        merchant_id=merchant_id,
        proposal_id=proposal_id,
        request=request,
        decision=ApprovalDecisionType.APPROVED,
        settings=settings,
    )


@router.post(
    "/proposals/{proposal_id}/reject",
    response_model=ProposalDecisionResponse,
    status_code=status.HTTP_201_CREATED,
)
async def reject_recovery_proposal(
    proposal_id: UUID,
    request: ProposalDecisionRequest,
    merchant_id: Annotated[UUID, Depends(require_merchant)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
) -> ProposalDecisionResponse:
    """Record an immutable rejection; rejected proposals can never execute."""

    return await _decide(
        session,
        merchant_id=merchant_id,
        proposal_id=proposal_id,
        request=request,
        decision=ApprovalDecisionType.REJECTED,
        settings=settings,
    )


@router.get("/incidents/{incident_id}/audit", response_model=list[AuditEvent])
async def get_incident_audit(
    incident_id: UUID,
    merchant_id: Annotated[UUID, Depends(require_merchant)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> list[AuditEvent]:
    """Return the append-only audit timeline for the owning merchant."""

    if await IncidentRepository(session).get(merchant_id, incident_id) is None:
        raise HTTPException(status_code=404, detail="Incident was not found")
    records = await RecoveryRepository(session).list_audit(merchant_id, incident_id=incident_id)
    return [
        AuditEvent(
            audit_id=record.id,
            merchant_id=record.merchant_id,
            correlation_id=record.correlation_id,
            incident_id=record.incident_id,
            plan_id=record.proposal_id,
            event_type=record.event_type,
            actor_type=record.actor_type,
            actor_id=record.actor_id,
            occurred_at=_as_utc(record.occurred_at),
            details=record.details,
        )
        for record in records
    ]


async def _decide(
    session: AsyncSession,
    *,
    merchant_id: UUID,
    proposal_id: UUID,
    request: ProposalDecisionRequest,
    decision: ApprovalDecisionType,
    settings: Settings,
) -> ProposalDecisionResponse:
    try:
        async with session.begin():
            return await RecoveryService(settings).decide(
                session,
                merchant_id=merchant_id,
                proposal_id=proposal_id,
                decision=decision,
                decided_by=request.decided_by,
                correlation_id=get_correlation_id(),
            )
    except RecoveryNotFoundError as error:
        raise HTTPException(status_code=404, detail=str(error)) from error
    except RecoveryConflictError as error:
        raise HTTPException(status_code=409, detail=str(error)) from error
    except RecoveryPolicyRejectedError as error:
        raise HTTPException(status_code=422, detail=str(error)) from error


def _incident_response(incident: Incident, evidence) -> DetectedIncidentResponse:
    return DetectedIncidentResponse(
        incident_id=incident.id,
        merchant_id=incident.merchant_id,
        incident_type=incident.incident_type,
        status=incident.status,
        currency=incident.currency,
        method=incident.method,
        bank=incident.bank,
        error_reason=incident.error_reason,
        detector_version=incident.detector_version,
        baseline_window_start=_as_utc(incident.baseline_window_start),
        current_window_start=_as_utc(incident.current_window_start),
        current_window_end=_as_utc(incident.current_window_end),
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
        evidence=[
            IncidentEvidenceResponse(
                evidence_id=item.id,
                kind=item.kind,
                summary=item.summary,
                source_reference=item.source_reference,
                details=item.details,
                created_at=_as_utc(item.created_at),
            )
            for item in evidence
        ],
    )


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)