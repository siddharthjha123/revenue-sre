"""Reusable builders for deterministic incident integration tests."""

from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from backend.app.config import Settings
from backend.app.database.models.incident import Incident
from backend.app.database.models.webhook_event import WebhookEvent, WebhookStatus
from backend.app.services.payment_event_pipeline import PaymentEventPipeline

MERCHANT_A = UUID("c56a4180-65aa-42ec-a945-5fd21dec0538")
MERCHANT_B = UUID("c56a4180-65aa-42ec-a945-5fd21dec0539")
CORRELATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ANCHOR = datetime(2026, 8, 29, 12, 0, tzinfo=UTC)


def detector_settings(**overrides) -> Settings:
    values = {
        "environment": "test",
        "merchant_id": MERCHANT_A,
        "razorpay_account_id": "acc_TEST001",
        "incident_current_window_minutes": 5,
        "incident_baseline_window_minutes": 30,
        "incident_minimum_attempts": 5,
        "incident_minimum_failures": 3,
        "incident_minimum_failure_rate": 0.5,
        "incident_minimum_rate_increase": 0.2,
        "incident_baseline_multiplier": 2.0,
        "recovery_proposal_cooldown_minutes": 0,
        "recovery_max_plan_amount_subunits": 100_000,
        "recovery_max_actions_per_plan": 5,
        "recovery_max_plan_lifetime_minutes": 60,
        "recovery_max_customer_contacts": 1,
    }
    values.update(overrides)
    return Settings(**values)


async def seed_failure_spike(
    session: AsyncSession,
    *,
    settings: Settings | None = None,
    merchant_id: UUID = MERCHANT_A,
) -> Incident:
    """Process a 10-payment batch that deterministically opens one incident."""

    pipeline = PaymentEventPipeline(settings or detector_settings())
    events: list[tuple[str, datetime, str, int]] = []
    for index in range(5):
        events.append(("captured", ANCHOR - timedelta(minutes=20 - index), f"BASE{index}", 5_000))
    events.extend(
        [
            ("captured", ANCHOR - timedelta(minutes=4), "CURS0", 7_000),
            ("captured", ANCHOR - timedelta(minutes=3), "CURS1", 8_000),
            ("failed", ANCHOR - timedelta(minutes=2), "CURF0", 10_000),
            ("failed", ANCHOR - timedelta(minutes=1), "CURF1", 20_000),
            ("failed", ANCHOR, "CURF2", 30_000),
        ]
    )
    for index, (payment_status, occurred_at, suffix, amount) in enumerate(events):
        event = _event(
            merchant_id=merchant_id,
            event_index=index,
            status=payment_status,
            occurred_at=occurred_at,
            payment_suffix=suffix,
            amount=amount,
        )
        session.add(event)
        await session.flush()
        await pipeline(event, session)
    incident = await session.scalar(select(Incident).where(Incident.merchant_id == merchant_id))
    assert incident is not None
    return incident


def _event(
    *,
    merchant_id: UUID,
    event_index: int,
    status: str,
    occurred_at: datetime,
    payment_suffix: str,
    amount: int,
) -> WebhookEvent:
    event_type = f"payment.{status}"
    payment_id = f"pay_{payment_suffix}"
    timestamp = int(occurred_at.timestamp())
    return WebhookEvent(
        correlation_id=CORRELATION_ID,
        merchant_id=merchant_id,
        razorpay_event_id=f"event_incident_{merchant_id.hex[-4:]}_{event_index}",
        razorpay_account_id="acc_TEST001",
        event_type=event_type,
        provider_event_at=occurred_at,
        payload_hash=f"{event_index:064x}",
        status=WebhookStatus.RECEIVED,
        payload={
            "entity": "event",
            "account_id": "acc_TEST001",
            "event": event_type,
            "created_at": timestamp,
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment_id,
                        "order_id": f"order_{payment_suffix}",
                        "amount": amount,
                        "currency": "INR",
                        "status": status,
                        "method": "upi",
                        "bank": "HDFC",
                        "captured": status == "captured",
                        "international": False,
                        "error_code": "BAD_REQUEST_ERROR" if status == "failed" else None,
                        "error_source": "bank" if status == "failed" else None,
                        "error_step": "payment_authorization" if status == "failed" else None,
                        "error_reason": "payment_timed_out" if status == "failed" else None,
                        "created_at": timestamp,
                    }
                }
            },
        },
    )
