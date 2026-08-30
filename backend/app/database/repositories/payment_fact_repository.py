"""Tenant-scoped persistence for immutable normalized payment facts."""

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.payment_event_fact import PaymentEventFact


class PaymentFactRepository:
    """Access append-only facts without owning the surrounding transaction."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get_by_webhook_event(self, webhook_event_id: UUID) -> PaymentEventFact | None:
        return await self._session.scalar(
            select(PaymentEventFact).where(PaymentEventFact.webhook_event_id == webhook_event_id)
        )

    async def add_once(self, fact: PaymentEventFact) -> tuple[PaymentEventFact, bool]:
        """Persist one fact idempotently, including concurrent worker retries."""

        fact_id = fact.id or uuid4()
        values = {
            column.name: getattr(fact, column.name)
            for column in PaymentEventFact.__table__.columns
            if column.name not in {"id", "recorded_at"}
        }
        values["id"] = fact_id
        dialect = self._session.get_bind().dialect.name
        statement = (
            postgresql_insert(PaymentEventFact)
            if dialect == "postgresql"
            else sqlite_insert(PaymentEventFact)
            if dialect == "sqlite"
            else insert(PaymentEventFact)
        )
        if dialect in {"postgresql", "sqlite"}:
            statement = statement.on_conflict_do_nothing(
                index_elements=[PaymentEventFact.webhook_event_id]
            )
        result = await self._session.execute(
            statement.values(**values).returning(PaymentEventFact.id)
        )
        created = result.scalar_one_or_none() is not None
        existing = await self.get_by_webhook_event(fact.webhook_event_id)
        if existing is None:
            raise RuntimeError("Payment fact insert did not resolve a fact")
        if existing.merchant_id != fact.merchant_id:
            raise RuntimeError("Payment fact webhook belongs to another merchant")
        return existing, created

    async def list_window(
        self,
        merchant_id: UUID,
        *,
        start_at: datetime,
        end_at: datetime,
    ) -> Sequence[PaymentEventFact]:
        result = await self._session.scalars(
            select(PaymentEventFact)
            .where(
                PaymentEventFact.merchant_id == merchant_id,
                PaymentEventFact.provider_event_at >= start_at,
                PaymentEventFact.provider_event_at <= end_at,
            )
            .order_by(PaymentEventFact.provider_event_at, PaymentEventFact.id)
        )
        return result.all()

    async def latest_event_at(self, merchant_id: UUID) -> datetime | None:
        return await self._session.scalar(
            select(PaymentEventFact.provider_event_at)
            .where(PaymentEventFact.merchant_id == merchant_id)
            .order_by(PaymentEventFact.provider_event_at.desc())
            .limit(1)
        )
