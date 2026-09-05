"""Minimal contracts for authenticated Razorpay webhook ingestion."""

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class SupportedWebhookEvent(StrEnum):
    """Payment events accepted by the first ingestion pipeline."""

    PAYMENT_FAILED = "payment.failed"
    PAYMENT_AUTHORIZED = "payment.authorized"
    PAYMENT_CAPTURED = "payment.captured"
    PAYMENT_LINK_PAID = "payment_link.paid"


class RazorpayWebhookEnvelope(BaseModel):
    """Provider envelope fields required before durable storage."""

    model_config = ConfigDict(extra="allow", str_strip_whitespace=True)

    entity: Literal["event"]
    account_id: str = Field(pattern=r"^acc_[A-Za-z0-9]+$", max_length=64)
    event: str = Field(min_length=1, max_length=128)
    contains: list[str] = Field(default_factory=list, max_length=32)
    payload: dict[str, Any]
    created_at: int = Field(ge=0)


class WebhookIngestionStatus(StrEnum):
    """Public acknowledgement states returned to Razorpay."""

    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    IGNORED = "ignored"


class WebhookIngestionResponse(BaseModel):
    """Small response that does not expose internal database identifiers."""

    model_config = ConfigDict(extra="forbid")

    status: WebhookIngestionStatus
