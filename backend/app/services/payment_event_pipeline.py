"""Atomic payment normalization, fact persistence, and incident detection."""

from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings
from ..database.models.payment_event_fact import PaymentEventFact
from ..database.models.webhook_event import WebhookEvent
from ..database.repositories.payment_fact_repository import PaymentFactRepository
from ..schemas.webhook import SupportedWebhookEvent
from .event_normalizer import PaymentEventNormalizer, normalize_payment_event
from .incident_detector import IncidentDetector
from .recovery_outcome_service import RecoveryOutcomeService


class PaymentEventPipeline:
    """Production worker handler for supported payment events."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._normalizer = PaymentEventNormalizer()
        self._detector = IncidentDetector(settings)
        self._outcomes = RecoveryOutcomeService()

    async def __call__(self, event: WebhookEvent, session: AsyncSession) -> None:
        normalized = normalize_payment_event(event)
        await PaymentFactRepository(session).add_once(
            PaymentEventFact(
                webhook_event_id=event.id,
                merchant_id=event.merchant_id,
                razorpay_event_id=event.razorpay_event_id,
                razorpay_account_id=event.razorpay_account_id,
                event_type=event.event_type,
                payment_id=normalized.payment_id,
                order_id=normalized.order_id,
                amount_subunits=normalized.amount_subunits,
                currency=normalized.currency,
                status=normalized.status,
                method=normalized.method,
                bank=normalized.bank,
                wallet=normalized.wallet,
                error_code=normalized.error_code,
                error_source=normalized.error_source,
                error_step=normalized.error_step,
                error_reason=normalized.error_reason,
                payment_created_at=normalized.created_at,
                provider_event_at=event.provider_event_at,
            )
        )
        await self._normalizer.normalize_and_persist(event, session)
        if event.event_type == SupportedWebhookEvent.PAYMENT_LINK_PAID:
            await self._outcomes.record_payment_link_paid(event, session)
        await self._detector.detect(
            session,
            merchant_id=event.merchant_id,
            correlation_id=event.correlation_id,
        )
