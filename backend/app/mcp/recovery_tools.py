"""Policy-gated proposal tools exposed to the TrueForge agent."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..config import Settings, get_settings
from ..database.base import AsyncSessionFactory
from ..database.models.incident import IncidentEvidenceRecord
from ..database.models.payment_attempt import PaymentAttempt
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
                            .join(
                                PaymentAttempt,
                                and_(
                                    PaymentAttempt.merchant_id == PaymentEventFact.merchant_id,
                                    PaymentAttempt.payment_id == PaymentEventFact.payment_id,
                                ),
                            )
                            .where(
                                IncidentEvidenceRecord.merchant_id == merchant_id,
                                IncidentEvidenceRecord.incident_id == incident_id,
                                IncidentEvidenceRecord.id.in_(verification.evidence_ids),
                                PaymentEventFact.status == PaymentStatus.FAILED,
                                PaymentAttempt.status == PaymentStatus.FAILED,
                            )
                            .order_by(
                                PaymentEventFact.provider_event_at,
                                PaymentEventFact.payment_id,
                            )
                        )
                    ).all()
                )
                if not facts:
                    raise NoRecoverablePaymentsError(
                        "Incident has no failed evidence payments available for proposal"
                    )
                selected_facts = self._select_bounded_facts(
                    facts,
                    maximum_amount_subunits=min(
                        self._settings.recovery_max_plan_amount_subunits,
                        verification.revenue_at_risk_subunits,
                    ),
                    maximum_actions=self._settings.recovery_max_actions_per_plan,
                )
                if not selected_facts:
                    raise NoRecoverablePaymentsError(
                        "No failed evidence payment fits the configured recovery limits"
                    )
                request = RecoveryProposalCreate(
                    actions=[
                        RecoveryActionCreate(
                            payment_id=fact.payment_id,
                            action_type=action_type,
                            amount_subunits=fact.amount_subunits,
                            rationale=rationale,
                        )
                        for fact in selected_facts
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
                    generation_metadata={
                        "eligible_payment_count": len({fact.payment_id for fact in facts}),
                        "selected_action_count": len(selected_facts),
                        "omitted_payment_count": len({fact.payment_id for fact in facts})
                        - len(selected_facts),
                    },
                )

    @staticmethod
    def _select_bounded_facts(
        facts: list[PaymentEventFact],
        *,
        maximum_amount_subunits: int,
        maximum_actions: int,
    ) -> list[PaymentEventFact]:
        """Select a deterministic unpaid subset that fits every hard limit.

        The oldest verified failures are considered first. Repeated failure
        events for the same payment produce one action, and a payment that does
        not fit the remaining amount budget is skipped rather than causing the
        complete proposal to be rejected.
        """

        selected: list[PaymentEventFact] = []
        selected_payment_ids: set[str] = set()
        selected_amount = 0
        for fact in facts:
            if fact.payment_id in selected_payment_ids:
                continue
            if len(selected) >= maximum_actions:
                break
            if selected_amount + fact.amount_subunits > maximum_amount_subunits:
                continue
            selected.append(fact)
            selected_payment_ids.add(fact.payment_id)
            selected_amount += fact.amount_subunits
        return selected

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
