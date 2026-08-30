"""Razorpay webhook HMAC verification against the exact request bytes."""

import hashlib
import hmac

from pydantic import SecretStr


class InvalidSignatureError(ValueError):
    """Raised when a supplied webhook signature is missing or invalid."""


class WebhookConfigurationError(RuntimeError):
    """Raised when the server cannot authenticate webhooks safely."""


def verify_razorpay_signature(
    raw_body: bytes,
    received_signature: str | None,
    webhook_secret: str | SecretStr | None,
) -> None:
    """Verify a Razorpay webhook using HMAC-SHA256 and constant-time comparison.

    Args:
        raw_body: The exact raw bytes of the incoming request body.
        received_signature: The value from the 'x-razorpay-signature' header.
        webhook_secret: The pre-shared secret key from the Razorpay dashboard.

    Raises:
        InvalidSignatureError: If the signature is missing, malformed, or incorrect.
        WebhookConfigurationError: If the webhook secret is not configured.
    """

    if not received_signature:
        raise InvalidSignatureError("Missing webhook signature")

    secret_value = (
        webhook_secret.get_secret_value()
        if isinstance(webhook_secret, SecretStr)
        else webhook_secret
    )
    if not secret_value:
        raise WebhookConfigurationError("Webhook secret is not configured")

    # Razorpay sends a 32-byte SHA-256 digest encoded as 64 hexadecimal
    # characters. Rejecting malformed input keeps the failure deterministic.
    if len(received_signature) != 64:
        raise InvalidSignatureError("Invalid webhook signature")
    try:
        bytes.fromhex(received_signature)
    except ValueError as error:
        raise InvalidSignatureError("Invalid webhook signature") from error

    expected_mac = hmac.new(
        key=secret_value.encode("utf-8"),
        msg=raw_body,
        digestmod=hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(expected_mac, received_signature):
        raise InvalidSignatureError("Invalid webhook signature")
