"""Policy-gated proposal tools exposed to the TrueForge agent."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings, get_settings
from ..database.base import AsyncSessionFactory
from ..database.models.incident import IncidentEvidenceRecord
from ..database.models.payment_event_fact import PaymentEventFact
from ..database.repositories.recovery_repository import RecoveryRepository
from ..schemas.payment import PaymentStatus
from ..schemas.recovery import (
    RecoveryActionCreate,
    RecoveryActionType,
    RecoveryProposalCreate,
    RecoveryProposalResponse,
)
from ..services.recovery_service import RecoveryNotFoundError, RecoveryService
from .incident_tools import IncidentInvestigationTools, IncidentNotFoundError


class EvidenceVerificationError(RuntimeError):
    """Raised when a proposal is attempted against inconsistent evidence."""


class NoRecoverablePaymentsError(RuntimeError):
    """Raised when an incident has no currently failed evidence payments."""


class RecoveryAgentTools:
    """Derive bounded proposal inputs from trusted incident evidence."""

    def __init__(
        self,
        settings: Settings | None = None,
        session_factory: async_sessionmaker[AsyncSession] | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._session_factory = session_factory or AsyncSessionFactory
        self._investigation = IncidentInvestigationTools(
            self._settings,
            self._session_factory,
        )

    async def create_bounded_proposal(
        self,
        *,
        incident_id: UUID,
        action_type: RecoveryActionType,
        rationale: str,
        expires_in_minutes: int,
    ) -> RecoveryProposalResponse:
        """Create a policy-reviewed proposal without executing Razorpay actions."""

        verification = await self._investigation.verify_incident_evidence(incident_id)
        if not verification.verified:
            raise EvidenceVerificationError("Incident evidence failed verification")
        merchant_id = self._investigation._merchant_id()
        async with self._session_factory() as session:
            async with session.begin():
                existing = await RecoveryRepository(session).get_active_incident_proposal(
                    merchant_id,
                    incident_id,
                )
                if existing is not None:
                    return await RecoveryService(self._settings).get_proposal(
                        session,
                        merchant_id=merchant_id,
                        proposal_id=existing.id,
                    )
                facts = list(
                    (
                        await session.scalars(
                            select(PaymentEventFact)
                            .join(
                                IncidentEvidenceRecord,
                                IncidentEvidenceRecord.payment_event_fact_id == PaymentEventFact.id,
                            )
                            .where(
                                IncidentEvidenceRecord.merchant_id == merchant_id,
                                IncidentEvidenceRecord.incident_id == incident_id,
                                PaymentEventFact.status == PaymentStatus.FAILED,
                            )
                            .order_by(PaymentEventFact.payment_id)
                        )
                    ).all()
                )
                if not facts:
                    raise NoRecoverablePaymentsError(
                        "Incident has no failed evidence payments available for proposal"
                    )
                request = RecoveryProposalCreate(
                    actions=[
                        RecoveryActionCreate(
                            payment_id=fact.payment_id,
                            action_type=action_type,
                            amount_subunits=fact.amount_subunits,
                            rationale=rationale,
                        )
                        for fact in facts
                    ],
                    evidence_ids=verification.evidence_ids,
                    currency=facts[0].currency,
                    maximum_customer_contacts=1,
                    expires_at=datetime.now(UTC)
                    + timedelta(
                        minutes=min(
                            expires_in_minutes,
                            self._settings.recovery_max_plan_lifetime_minutes,
                        )
                    ),
                    created_by="trueforge-revenue-sre-agent",
                )
                return await RecoveryService(self._settings).create_proposal(
                    session,
                    merchant_id=merchant_id,
                    incident_id=incident_id,
                    request=request,
                    correlation_id=uuid4(),
                )

    async def get_proposal(self, proposal_id: UUID) -> RecoveryProposalResponse:
        """Read one merchant-owned proposal and its current approval status."""

        merchant_id = self._investigation._merchant_id()
        async with self._session_factory() as session:
            return await RecoveryService(self._settings).get_proposal(
                session,
                merchant_id=merchant_id,
                proposal_id=proposal_id,
            )


__all__ = [
    "EvidenceVerificationError",
    "IncidentNotFoundError",
    "NoRecoverablePaymentsError",
    "RecoveryAgentTools",
    "RecoveryNotFoundError",
]