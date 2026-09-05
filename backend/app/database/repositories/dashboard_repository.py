"""Efficient tenant-scoped aggregates for the merchant dashboard."""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ...schemas.incident import IncidentStatus
from ..models.incident import Incident
from ..models.payment_attempt import PaymentAttempt

ACTIONABLE_INCIDENT_STATUSES = (IncidentStatus.OPEN, IncidentStatus.INVESTIGATING)


@dataclass(frozen=True, slots=True)
class MoneyTotal:
    currency: str
    amount_subunits: int


@dataclass(frozen=True, slots=True)
class DashboardSnapshot:
    total_payment_attempts: int
    captured_payment_count: int
    captured_revenue_today: Sequence[MoneyTotal]
    total_incident_count: int
    open_incident_count: int
    open_revenue_at_risk: Sequence[MoneyTotal]


class DashboardRepository:
    """Read-only dashboard aggregates, always bounded to one merchant."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def summarize(
        self,
        merchant_id: UUID,
        *,
        reporting_day_start: datetime,
        reporting_day_end: datetime,
    ) -> DashboardSnapshot:
        payment_counts = (
            await self._session.execute(
                select(
                    func.count(PaymentAttempt.id),
                    func.coalesce(
                        func.sum(case((PaymentAttempt.captured.is_(True), 1), else_=0)),
                        0,
                    ),
                ).where(PaymentAttempt.merchant_id == merchant_id)
            )
        ).one()

        captured_revenue_today = await self._money_totals(
            select(
                PaymentAttempt.currency,
                func.sum(PaymentAttempt.amount_subunits),
            )
            .where(
                PaymentAttempt.merchant_id == merchant_id,
                PaymentAttempt.captured.is_(True),
                PaymentAttempt.provider_created_at >= reporting_day_start,
                PaymentAttempt.provider_created_at < reporting_day_end,
            )
            .group_by(PaymentAttempt.currency)
        )

        incident_counts = (
            await self._session.execute(
                select(
                    func.count(Incident.id),
                    func.coalesce(
                        func.sum(
                            case((Incident.status.in_(ACTIONABLE_INCIDENT_STATUSES), 1), else_=0)
                        ),
                        0,
                    ),
                ).where(Incident.merchant_id == merchant_id)
            )
        ).one()

        open_revenue_at_risk = await self._money_totals(
            select(
                Incident.currency,
                func.sum(Incident.revenue_at_risk_subunits),
            )
            .where(
                Incident.merchant_id == merchant_id,
                Incident.status.in_(ACTIONABLE_INCIDENT_STATUSES),
            )
            .group_by(Incident.currency)
        )

        return DashboardSnapshot(
            total_payment_attempts=int(payment_counts[0]),
            captured_payment_count=int(payment_counts[1]),
            captured_revenue_today=captured_revenue_today,
            total_incident_count=int(incident_counts[0]),
            open_incident_count=int(incident_counts[1]),
            open_revenue_at_risk=open_revenue_at_risk,
        )

    async def _money_totals(self, statement) -> list[MoneyTotal]:
        rows = (await self._session.execute(statement)).all()
        return [
            MoneyTotal(currency=currency, amount_subunits=int(amount_subunits or 0))
            for currency, amount_subunits in rows
        ]
