"""Normalized payment contracts used by Revenue SRE.

Razorpay remains the source of truth for payment execution. These models store
only the fields Revenue SRE needs for incident detection and recovery safety.
Amounts are represented in currency subunits (paise for INR), matching the
Razorpay API and avoiding floating-point rounding errors.
"""

from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

RazorpayPaymentId = Annotated[
    str,
    StringConstraints(pattern=r"^pay_[A-Za-z0-9]+$", min_length=5, max_length=64),
]
RazorpayOrderId = Annotated[
    str,
    StringConstraints(pattern=r"^order_[A-Za-z0-9]+$", min_length=7, max_length=64),
]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class PaymentStatus(StrEnum):
    """Razorpay payment lifecycle states relevant to this application."""

    CREATED = "created"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    REFUNDED = "refunded"
    FAILED = "failed"


class PaymentMethod(StrEnum):
    """Normalized payment methods; OTHER preserves forward compatibility."""

    CARD = "card"
    UPI = "upi"
    NETBANKING = "netbanking"
    WALLET = "wallet"
    EMI = "emi"
    CARDLESS_EMI = "cardless_emi"
    PAYLATER = "paylater"
    OTHER = "other"


class ErrorSource(StrEnum):
    """Systems Razorpay may identify as the source of a payment failure."""

    CUSTOMER = "customer"
    BUSINESS = "business"
    BANK = "bank"
    GATEWAY = "gateway"
    INTERNAL = "internal"
    UNKNOWN = "unknown"


class PaymentAttemptBase(BaseModel):
    """Provider-neutral representation of one Razorpay payment attempt."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )

    merchant_id: UUID = Field(description="Revenue SRE tenant identifier.")
    order_id: RazorpayOrderId | None = Field(
        default=None,
        description="Associated Razorpay order ID, when the payment has one.",
    )
    amount_subunits: int = Field(
        gt=0,
        description="Amount in the smallest currency unit; paise for INR.",
    )
    currency: CurrencyCode
    method: PaymentMethod
    checkout_version: str | None = Field(
        default=None,
        max_length=64,
        description="Merchant-provided checkout release identifier.",
    )


class PaymentAttemptResponse(PaymentAttemptBase):
    """Stored payment attempt returned by the Revenue SRE API."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        from_attributes=True,
        json_schema_extra={
            "example": {
                "merchant_id": "c56a4180-65aa-42ec-a945-5fd21dec0538",
                "payment_id": "pay_TU17WFeXwe0HQr",
                "order_id": "order_TU0xExample123",
                "amount_subunits": 100000,
                "currency": "INR",
                "status": "captured",
                "method": "netbanking",
                "created_at": "2026-08-26T12:30:00Z",
                "error_source": None,
                "error_code": None,
                "error_step": None,
                "error_reason": None,
                "checkout_version": "2.1.0",
            }
        },
    )

    payment_id: RazorpayPaymentId
    status: PaymentStatus
    created_at: AwareDatetime
    captured: bool = False
    international: bool = False
    error_code: str | None = Field(default=None, max_length=128)
    error_description: str | None = Field(default=None, max_length=512)
    error_source: ErrorSource | None = None
    error_step: str | None = Field(default=None, max_length=128)
    error_reason: str | None = Field(default=None, max_length=128)
