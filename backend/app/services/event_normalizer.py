"""Normalize authenticated Razorpay events into the current payment projection."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import ValidationError
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.models.payment_attempt import PaymentAttempt
from ..database.models.webhook_event import WebhookEvent
from ..database.repositories.payment_repository import PaymentRepository
from ..schemas.payment import (
    ErrorSource,
    PaymentAttemptResponse,
    PaymentMethod,
    PaymentStatus,
)
from ..schemas.webhook import SupportedWebhookEvent

EVENT_STATUS = {
    SupportedWebhookEvent.PAYMENT_FAILED.value: PaymentStatus.FAILED,
    SupportedWebhookEvent.PAYMENT_AUTHORIZED.value: PaymentStatus.AUTHORIZED,
    SupportedWebhookEvent.PAYMENT_CAPTURED.value: PaymentStatus.CAPTURED,
}
VALID_FORWARD_TRANSITIONS = {
    (PaymentStatus.CREATED, PaymentStatus.FAILED),
    (PaymentStatus.CREATED, PaymentStatus.AUTHORIZED),
    (PaymentStatus.CREATED, PaymentStatus.CAPTURED),
    (PaymentStatus.FAILED, PaymentStatus.AUTHORIZED),
    (PaymentStatus.FAILED, PaymentStatus.CAPTURED),
    (PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED),
}


class PaymentNormalizationError(ValueError):
    """A permanent, sanitized failure caused by invalid provider event data."""

    def __init__(self, *, code: str, safe_message: str) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message


class PaymentProjectionAction(StrEnum):
    """How one event affected the current payment projection."""

    CREATED = "created"
    UPDATED = "updated"
    IGNORED_STALE = "ignored_stale"
    IGNORED_REGRESSION = "ignored_regression"
    IGNORED_REPLAY = "ignored_replay"


class PaymentTransitionDecision(StrEnum):
    """Pure state-machine result independent of database persistence."""

    APPLY = "apply"
    STALE = "stale"
    REGRESSION = "regression"


def decide_payment_transition(
    *,
    current_status: PaymentStatus,
    last_applied_event_at: datetime,
    incoming_status: PaymentStatus,
    incoming_event_at: datetime,
) -> PaymentTransitionDecision:
    """Decide whether an event may alter the current payment projection."""

    current_event_at = _as_utc(last_applied_event_at)
    event_at = _as_utc(incoming_event_at)
    if incoming_status == current_status:
        return (
            PaymentTransitionDecision.APPLY
            if event_at > current_event_at
            else PaymentTransitionDecision.STALE
        )
    if (current_status, incoming_status) in VALID_FORWARD_TRANSITIONS:
        return PaymentTransitionDecision.APPLY
    return PaymentTransitionDecision.REGRESSION


def normalize_payment_event(event: WebhookEvent) -> PaymentAttemptResponse:
    """Map a sanitized supported webhook into the strict payment contract.

    Unknown payment methods intentionally become ``OTHER`` so a new Razorpay
    method cannot crash or indefinitely retry the durable worker.
    """

    expected_status = EVENT_STATUS.get(event.event_type)
    if expected_status is None:
        raise PaymentNormalizationError(
            code="unsupported_payment_event",
            safe_message="Webhook event type is not supported by the payment normalizer",
        )

    payment = _payment_entity(event.payload)
    raw_status = payment.get("status")
    if raw_status != expected_status.value:
        raise PaymentNormalizationError(
            code="payment_event_status_mismatch",
            safe_message="Webhook type and payment status do not match",
        )

    try:
        return PaymentAttemptResponse.model_validate(
            {
                "merchant_id": event.merchant_id,
                "payment_id": payment.get("id"),
                "order_id": payment.get("order_id"),
                "amount_subunits": payment.get("amount"),
                "currency": payment.get("currency"),
                "status": raw_status,
                "method": _payment_method(payment.get("method")),
                "bank": payment.get("bank"),
                "wallet": payment.get("wallet"),
                "captured": payment.get("captured", False),
                "international": payment.get("international", False),
                "error_code": payment.get("error_code"),
                "error_description": payment.get("error_description"),
                "error_source": _error_source(payment.get("error_source")),
                "error_step": payment.get("error_step"),
                "error_reason": payment.get("error_reason"),
                "created_at": _unix_datetime(payment.get("created_at")),
            }
        )
    except ValidationError as error:
        raise PaymentNormalizationError(
            code="invalid_payment_payload",
            safe_message="Payment webhook contains invalid required fields",
        ) from error


class PaymentEventNormalizer:
    """Persist a safe, idempotent current-state projection for payment events."""

    async def __call__(self, event: WebhookEvent, session: AsyncSession) -> None:
        """Allow this service to be injected directly into ``WebhookJobWorker``."""

        await self.normalize_and_persist(event, session)

    async def normalize_and_persist(
        self,
        event: WebhookEvent,
        session: AsyncSession,
    ) -> PaymentProjectionAction:
        """Create or update one tenant-scoped payment without losing event history."""

        normalized = normalize_payment_event(event)
        event_at = _as_utc(event.provider_event_at)
        repository = PaymentRepository(session)
        current = await repository.get_by_payment_id(
            event.merchant_id,
            normalized.payment_id,
            for_update=True,
        )

        if current is None:
            await repository.add(
                PaymentAttempt(
                    merchant_id=normalized.merchant_id,
                    razorpay_account_id=event.razorpay_account_id,
                    payment_id=normalized.payment_id,
                    order_id=normalized.order_id,
                    amount_subunits=normalized.amount_subunits,
                    currency=normalized.currency,
                    status=normalized.status,
                    method=normalized.method,
                    bank=normalized.bank,
                    wallet=normalized.wallet,
                    captured=normalized.captured,
                    international=normalized.international,
                    error_code=normalized.error_code,
                    error_description=normalized.error_description,
                    error_source=normalized.error_source,
                    error_step=normalized.error_step,
                    error_reason=normalized.error_reason,
                    checkout_version=normalized.checkout_version,
                    last_razorpay_event_id=event.razorpay_event_id,
                    provider_created_at=normalized.created_at,
                    last_applied_event_at=event_at,
                )
            )
            return PaymentProjectionAction.CREATED

        self._verify_payment_identity(current, normalized)
        if current.last_razorpay_event_id == event.razorpay_event_id:
            return PaymentProjectionAction.IGNORED_REPLAY
        transition = decide_payment_transition(
            current_status=current.status,
            last_applied_event_at=current.last_applied_event_at,
            incoming_status=normalized.status,
            incoming_event_at=event_at,
        )
        if transition == PaymentTransitionDecision.STALE:
            return PaymentProjectionAction.IGNORED_STALE
        if transition == PaymentTransitionDecision.REGRESSION:
            return PaymentProjectionAction.IGNORED_REGRESSION

        self._apply(current, normalized, event, event_at)
        await session.flush()
        return PaymentProjectionAction.UPDATED

    @staticmethod
    def _verify_payment_identity(
        current: PaymentAttempt,
        incoming: PaymentAttemptResponse,
    ) -> None:
        if (
            current.amount_subunits != incoming.amount_subunits
            or current.currency != incoming.currency
            or _as_utc(current.provider_created_at) != _as_utc(incoming.created_at)
        ):
            raise PaymentNormalizationError(
                code="payment_identity_conflict",
                safe_message="Payment identity fields conflict with the stored payment",
            )

    @staticmethod
    def _apply(
        current: PaymentAttempt,
        incoming: PaymentAttemptResponse,
        event: WebhookEvent,
        event_at: datetime,
    ) -> None:
        current.razorpay_account_id = event.razorpay_account_id
        current.order_id = incoming.order_id
        current.status = incoming.status
        current.method = incoming.method
        current.bank = incoming.bank
        current.wallet = incoming.wallet
        current.captured = incoming.captured
        current.international = incoming.international
        current.error_code = incoming.error_code
        current.error_description = incoming.error_description
        current.error_source = incoming.error_source
        current.error_step = incoming.error_step
        current.error_reason = incoming.error_reason
        current.checkout_version = incoming.checkout_version
        current.last_razorpay_event_id = event.razorpay_event_id
        current.last_applied_event_at = event_at


def _payment_entity(payload: dict[str, Any]) -> dict[str, Any]:
    payment_wrapper = payload.get("payload")
    if not isinstance(payment_wrapper, dict):
        raise PaymentNormalizationError(
            code="payment_entity_missing",
            safe_message="Webhook does not contain a payment entity",
        )
    payment = payment_wrapper.get("payment")
    if not isinstance(payment, dict) or not isinstance(payment.get("entity"), dict):
        raise PaymentNormalizationError(
            code="payment_entity_missing",
            safe_message="Webhook does not contain a payment entity",
        )
    return payment["entity"]


def _payment_method(value: object) -> PaymentMethod:
    try:
        return PaymentMethod(str(value).lower())
    except ValueError:
        return PaymentMethod.OTHER


def _error_source(value: object) -> ErrorSource | None:
    if value is None:
        return None
    try:
        return ErrorSource(str(value).lower())
    except ValueError:
        return ErrorSource.UNKNOWN


def _unix_datetime(value: object) -> datetime:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise PaymentNormalizationError(
            code="invalid_payment_timestamp",
            safe_message="Payment webhook timestamp is invalid",
        )
    try:
        return datetime.fromtimestamp(value, UTC)
    except (OverflowError, OSError, ValueError) as error:
        raise PaymentNormalizationError(
            code="invalid_payment_timestamp",
            safe_message="Payment webhook timestamp is invalid",
        ) from error


def _as_utc(value: datetime) -> datetime:
    """Normalize database timestamps; SQLite drops timezone metadata in tests."""

    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)
