"""Attribute authenticated Razorpay Payment Link webhooks to recovery actions."""

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models.recovery import AuditRecord
from ..database.models.webhook_event import WebhookEvent
from ..database.repositories.incident_repository import IncidentRepository
from ..database.repositories.recovery_repository import RecoveryRepository
from ..schemas.audit import AuditActorType, AuditEventType
from ..schemas.incident import IncidentStatus


class RecoveryOutcomeService:
    async def record_payment_link_paid(
        self,
        event: WebhookEvent,
        session: AsyncSession,
    ) -> None:
        link, payment = _entities(event.payload)
        notes = link.get("notes")
        if not isinstance(notes, dict):
            return
        try:
            action_id = UUID(str(notes["revenue_sre_action_id"]))
        except (KeyError, TypeError, ValueError):
            return
        link_id = link.get("id")
        reference_id = link.get("reference_id")
        if not isinstance(link_id, str) or not isinstance(reference_id, str):
            return

        repository = RecoveryRepository(session)
        action = await repository.get_action_for_payment_link(
            event.merchant_id,
            action_id=action_id,
            payment_link_id=link_id,
            reference_id=reference_id,
        )
        if action is None or action.recovered_at is not None:
            return
        proposal = await repository.get_proposal(event.merchant_id, action.proposal_id)
        if proposal is None:
            return
        paid_amount = link.get("amount_paid")
        if (
            link.get("status") != "paid"
            or paid_amount != action.amount_subunits
            or link.get("currency") != proposal.currency
        ):
            return
        payment_id = payment.get("id")
        if not isinstance(payment_id, str):
            return

        recovered_at = datetime.now(UTC)
        action.recovered_payment_id = payment_id
        action.recovered_amount_subunits = paid_amount
        action.recovered_at = recovered_at
        await repository.add_audit(
            AuditRecord(
                merchant_id=event.merchant_id,
                correlation_id=event.correlation_id,
                incident_id=proposal.incident_id,
                proposal_id=proposal.id,
                event_type=AuditEventType.OUTCOME_VERIFIED,
                actor_type=AuditActorType.RAZORPAY,
                actor_id="razorpay-webhook",
                occurred_at=recovered_at,
                details={
                    "action_id": str(action.id),
                    "payment_link_id": link_id,
                    "payment_id": payment_id,
                    "recovered_count": 1,
                    "recovered_amount_subunits": paid_amount,
                    "currency": proposal.currency,
                },
            )
        )
        actions = list(await repository.list_actions(proposal.id))
        incident = await IncidentRepository(session).get(event.merchant_id, proposal.incident_id)
        if incident is not None:
            recovered_total = sum(item.recovered_amount_subunits or 0 for item in actions)
            incident.status = (
                IncidentStatus.RESOLVED
                if recovered_total >= incident.revenue_at_risk_subunits
                else IncidentStatus.INVESTIGATING
            )
        await session.flush()


def _entities(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    wrapper = payload.get("payload")
    if not isinstance(wrapper, dict):
        return {}, {}
    link_wrapper = wrapper.get("payment_link")
    payment_wrapper = wrapper.get("payment")
    link = link_wrapper.get("entity") if isinstance(link_wrapper, dict) else None
    payment = payment_wrapper.get("entity") if isinstance(payment_wrapper, dict) else None
    return (link if isinstance(link, dict) else {}, payment if isinstance(payment, dict) else {})
