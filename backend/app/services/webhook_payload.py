"""PII-minimizing projection of authenticated Razorpay webhook payloads."""

from typing import Any

from ..schemas.webhook import RazorpayWebhookEnvelope, SupportedWebhookEvent


class InvalidWebhookPayloadError(ValueError):
    """Raised when a supported event lacks its required payment structure."""


PAYMENT_FIELD_ALLOWLIST = frozenset(
    {
        "id",
        "entity",
        "amount",
        "currency",
        "status",
        "order_id",
        "international",
        "method",
        "captured",
        "bank",
        "wallet",
        "error_code",
        "error_description",
        "error_source",
        "error_step",
        "error_reason",
        "created_at",
    }
)
SUPPORTED_EVENT_VALUES = frozenset(event.value for event in SupportedWebhookEvent)


def sanitize_webhook_payload(envelope: RazorpayWebhookEnvelope) -> dict[str, Any]:
    """Return only fields needed for payment processing and incident evidence.

    Email, contact, card, VPA, notes and other unneeded provider fields are
    omitted by construction rather than removed from an unrestricted copy.
    """

    sanitized: dict[str, Any] = {
        "entity": envelope.entity,
        "account_id": envelope.account_id,
        "event": envelope.event,
        "contains": envelope.contains,
        "created_at": envelope.created_at,
    }

    if envelope.event not in SUPPORTED_EVENT_VALUES:
        return sanitized

    payment_wrapper = envelope.payload.get("payment")
    if not isinstance(payment_wrapper, dict):
        raise InvalidWebhookPayloadError("Supported event is missing payment payload")
    payment_entity = payment_wrapper.get("entity")
    if not isinstance(payment_entity, dict):
        raise InvalidWebhookPayloadError("Supported event is missing payment entity")
    if not isinstance(payment_entity.get("id"), str):
        raise InvalidWebhookPayloadError("Payment entity is missing its provider ID")

    sanitized_payment = {
        key: value for key, value in payment_entity.items() if key in PAYMENT_FIELD_ALLOWLIST
    }
    sanitized["payload"] = {"payment": {"entity": sanitized_payment}}
    return sanitized
