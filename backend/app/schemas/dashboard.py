"""Merchant dashboard summary contracts."""

from datetime import date

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from .payment import CurrencyCode


class CurrencyAmount(BaseModel):
    """An integer money total in one currency's smallest unit."""

    model_config = ConfigDict(extra="forbid")

    currency: CurrencyCode
    amount_subunits: int = Field(ge=0)


class DashboardSummary(BaseModel):
    """Real merchant KPIs used by the command-center header."""

    model_config = ConfigDict(extra="forbid")

    total_payment_attempts: int = Field(ge=0)
    captured_payment_count: int = Field(ge=0)
    captured_revenue_today: list[CurrencyAmount]
    total_incident_count: int = Field(ge=0)
    open_incident_count: int = Field(ge=0)
    open_revenue_at_risk: list[CurrencyAmount]
    reporting_timezone: str
    reporting_day: date
    generated_at: AwareDatetime
