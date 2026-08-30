"""Tenant-scoped persistence operations for normalized payment attempts."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.payment_attempt import PaymentAttempt


class PaymentRepository:
    """Database access for current payment state.

    This repository never commits and never decides payment state transitions.
    The normalization service owns those business rules and the caller owns the
    transaction.
    """

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, payment: PaymentAttempt) -> PaymentAttempt:
        """Stage and flush a new payment attempt within the caller's transaction."""

        self._session.add(payment)
        await self._session.flush()
        return payment

    async def get_by_payment_id(
        self,
        merchant_id: UUID,
        payment_id: str,
        *,
        for_update: bool = False,
    ) -> PaymentAttempt | None:
        """Retrieve one payment under a mandatory merchant boundary."""

        statement = select(PaymentAttempt).where(
            PaymentAttempt.merchant_id == merchant_id,
            PaymentAttempt.payment_id == payment_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return await self._session.scalar(statement)

    async def list_recent(
        self,
        merchant_id: UUID,
        *,
        limit: int = 100,
    ) -> Sequence[PaymentAttempt]:
        """Return recent payments for one merchant with a bounded result size."""

        bounded_limit = min(max(limit, 1), 500)
        result = await self._session.scalars(
            select(PaymentAttempt)
            .where(PaymentAttempt.merchant_id == merchant_id)
            .order_by(PaymentAttempt.provider_created_at.desc())
            .limit(bounded_limit)
        )
        return result.all()
