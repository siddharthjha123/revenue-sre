"""Unit tests for raw-body Razorpay HMAC verification."""

import hashlib
import hmac

import pytest
from pydantic import SecretStr

from backend.app.services.webhook_signature import (
    InvalidSignatureError,
    WebhookConfigurationError,
    verify_razorpay_signature,
)

SECRET = "test-webhook-secret"
RAW_BODY = b'{"event":"payment.failed","amount":100000}'


def signature_for(body: bytes, secret: str = SECRET) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.mark.parametrize("secret", [SECRET, SecretStr(SECRET)])
def test_valid_signature_accepts_plain_and_secret_string(secret: str | SecretStr) -> None:
    verify_razorpay_signature(RAW_BODY, signature_for(RAW_BODY), secret)


def test_signature_is_bound_to_exact_raw_bytes() -> None:
    signature = signature_for(RAW_BODY)
    reformatted_body = b'{"event": "payment.failed", "amount": 100000}'

    with pytest.raises(InvalidSignatureError):
        verify_razorpay_signature(reformatted_body, signature, SECRET)


@pytest.mark.parametrize("signature", [None, "", "not-hex", "a" * 63])
def test_missing_or_malformed_signature_is_rejected(signature: str | None) -> None:
    with pytest.raises(InvalidSignatureError):
        verify_razorpay_signature(RAW_BODY, signature, SECRET)


def test_missing_server_secret_is_configuration_failure() -> None:
    with pytest.raises(WebhookConfigurationError):
        verify_razorpay_signature(RAW_BODY, signature_for(RAW_BODY), None)
