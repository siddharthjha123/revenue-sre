"""Tenant-safe persistence operations for the durable webhook inbox."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import insert, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.webhook_event import WebhookEvent, WebhookStatus


class WebhookTenantMismatchError(RuntimeError):
    """Raised when a provider event ID is unexpectedly reused across tenants."""


@dataclass(frozen=True, slots=True)
class NewWebhookEvent:
    """Validated, sanitized values required to persist an incoming event."""

    correlation_id: UUID
    merchant_id: UUID
    razorpay_event_id: str
    razorpay_account_id: str
    event_type: str
    provider_event_at: datetime
    payload_hash: str
    payload: dict[str, Any]


@dataclass(frozen=True, slots=True)
class WebhookInsertResult:
    """Result of an idempotent inbox insertion."""

    event: WebhookEvent
    created: bool


class WebhookRepository:
    """Database access for webhook events; transaction ownership stays upstream."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def insert_once(self, values: NewWebhookEvent) -> WebhookInsertResult:
        """Insert an event exactly once under concurrent provider retries.

        The nested transaction isolates a unique-constraint violation so a
        duplicate does not invalidate the caller's outer transaction.
        """

        event_id = uuid4()
        event_values = {
            "id": event_id,
            "correlation_id": values.correlation_id,
            "merchant_id": values.merchant_id,
            "razorpay_event_id": values.razorpay_event_id,
            "razorpay_account_id": values.razorpay_account_id,
            "event_type": values.event_type,
            "provider_event_at": values.provider_event_at,
            "payload_hash": values.payload_hash,
            "payload": values.payload,
            "status": WebhookStatus.RECEIVED,
        }
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(WebhookEvent)
        elif dialect == "sqlite":
            statement = sqlite_insert(WebhookEvent)
        else:
            statement = insert(WebhookEvent)

        if dialect in {"postgresql", "sqlite"}:
            statement = statement.on_conflict_do_nothing(
                index_elements=[WebhookEvent.razorpay_event_id]
            )
        result = await self._session.execute(
            statement.values(**event_values).returning(WebhookEvent.id)
        )
        inserted_id = result.scalar_one_or_none()
        existing = await self.get_by_event_id(values.razorpay_event_id)
        if existing is None:
            raise RuntimeError("Webhook insert did not return or resolve an event")
        if existing.merchant_id != values.merchant_id:
            raise WebhookTenantMismatchError(
                "Razorpay event ID already belongs to another merchant"
            )
        return WebhookInsertResult(event=existing, created=inserted_id is not None)

    async def get_by_event_id(self, razorpay_event_id: str) -> WebhookEvent | None:
        """Find an event by its globally unique Razorpay delivery ID."""

        return await self._session.scalar(
            select(WebhookEvent).where(WebhookEvent.razorpay_event_id == razorpay_event_id)
        )

    async def get_for_merchant(
        self,
        merchant_id: UUID,
        event_id: UUID,
    ) -> WebhookEvent | None:
        """Retrieve one event without crossing the authenticated tenant boundary."""

        return await self._session.scalar(
            select(WebhookEvent).where(
                WebhookEvent.id == event_id,
                WebhookEvent.merchant_id == merchant_id,
            )
        )

    async def mark_processed(self, event: WebhookEvent) -> None:
        """Mark an inbox event complete after its job succeeds."""

        event.status = WebhookStatus.PROCESSED
        event.processed_at = datetime.now(UTC)
        event.failure_reason = None
        await self._session.flush()

    async def mark_ignored(self, event: WebhookEvent, reason: str) -> None:
        """Finish an authenticated but unsupported event without retrying it."""

        event.status = WebhookStatus.IGNORED
        event.processed_at = datetime.now(UTC)
        event.failure_reason = reason[:2000]
        await self._session.flush()

    async def mark_failed(
        self,
        event: WebhookEvent,
        *,
        reason: str,
    ) -> None:
        """Mark an inbox event failed after its processing job is dead-lettered."""

        event.status = WebhookStatus.FAILED
        event.failure_reason = reason[:2000]
        event.processed_at = datetime.now(UTC)
        await self._session.flush()
