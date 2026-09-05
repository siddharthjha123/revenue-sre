"""Merchant-scoped command-center KPI endpoint."""

from datetime import UTC, datetime, time, timedelta, timezone
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from ..database.base import get_db_session
from ..database.repositories.dashboard_repository import DashboardRepository
from ..schemas.dashboard import CurrencyAmount, DashboardSummary
from .dependencies import require_merchant

router = APIRouter(tags=["dashboard"])

REPORTING_TIMEZONE_NAME = "Asia/Kolkata"
REPORTING_TIMEZONE = timezone(timedelta(hours=5, minutes=30), REPORTING_TIMEZONE_NAME)


@router.get("/dashboard/summary", response_model=DashboardSummary)
async def get_dashboard_summary(
    merchant_id: Annotated[UUID, Depends(require_merchant)],
    session: Annotated[AsyncSession, Depends(get_db_session)],
) -> DashboardSummary:
    """Return real payment and incident aggregates for the current merchant."""

    generated_at = datetime.now(UTC)
    reporting_day = generated_at.astimezone(REPORTING_TIMEZONE).date()
    day_start_local = datetime.combine(reporting_day, time.min, tzinfo=REPORTING_TIMEZONE)
    day_start = day_start_local.astimezone(UTC)
    day_end = (day_start_local + timedelta(days=1)).astimezone(UTC)

    snapshot = await DashboardRepository(session).summarize(
        merchant_id,
        reporting_day_start=day_start,
        reporting_day_end=day_end,
    )
    return DashboardSummary(
        total_payment_attempts=snapshot.total_payment_attempts,
        captured_payment_count=snapshot.captured_payment_count,
        captured_revenue_today=[
            CurrencyAmount(currency=item.currency, amount_subunits=item.amount_subunits)
            for item in snapshot.captured_revenue_today
        ],
        total_incident_count=snapshot.total_incident_count,
        open_incident_count=snapshot.open_incident_count,
        open_revenue_at_risk=[
            CurrencyAmount(currency=item.currency, amount_subunits=item.amount_subunits)
            for item in snapshot.open_revenue_at_risk
        ],
        reporting_timezone=REPORTING_TIMEZONE_NAME,
        reporting_day=reporting_day,
        generated_at=generated_at,
    )
