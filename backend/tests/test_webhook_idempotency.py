"""Mandatory API concurrency and tenant-isolation proofs for webhook ingestion."""

import asyncio
import hashlib
import hmac
import json
from collections.abc import AsyncGenerator
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
import pytest_asyncio
from pydantic import SecretStr
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.config import Settings, get_settings
from backend.app.database.base import Base, get_db_session
from backend.app.database.models import ProcessingJob, WebhookEvent
from backend.app.database.repositories.webhook_repository import NewWebhookEvent, WebhookRepository
from backend.app.main import app

SECRET = "concurrent-webhook-secret"
MERCHANT_A = UUID("c56a4180-65aa-42ec-a945-5fd21dec0538")
MERCHANT_B = UUID("c56a4180-65aa-42ec-a945-5fd21dec0539")
CORRELATION_ID = UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
ACCOUNT_ID = "acc_TEST001"


@pytest_asyncio.fixture
async def concurrent_harness() -> AsyncGenerator[
    tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]], None
]:
    """Use separate request sessions against one WAL-enabled SQLite database."""

    database_file = Path(__file__).parent / f".idempotency-{uuid4()}.db"
    database_path = database_file.resolve().as_posix()
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{database_path}",
        connect_args={"timeout": 10},
    )
    async with engine.begin() as connection:
        await connection.execute(text("PRAGMA journal_mode=WAL"))
        await connection.execute(text("PRAGMA busy_timeout=10000"))
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        _env_file=None,
        database_url=f"sqlite+aiosqlite:///{database_path}",
        razorpay_webhook_secret=SecretStr(SECRET),
        merchant_id=MERCHANT_A,
        razorpay_account_id=ACCOUNT_ID,
    )

    async def override_session() -> AsyncGenerator[AsyncSession, None]:
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db_session] = override_session
    app.dependency_overrides[get_settings] = lambda: settings
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client, factory

    app.dependency_overrides.clear()
    await engine.dispose()
    database_file.unlink(missing_ok=True)


def webhook_body() -> bytes:
    payload = {
        "entity": "event",
        "account_id": ACCOUNT_ID,
        "event": "payment.failed",
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": "pay_TU17WFeXwe0HQr",
                    "amount": 100000,
                    "currency": "INR",
                    "status": "failed",
                    "method": "netbanking",
                    "captured": False,
                    "international": False,
                    "created_at": 1787664086,
                }
            }
        },
        "created_at": 1787664087,
    }
    return json.dumps(payload, separators=(",", ":")).encode()


@pytest.mark.asyncio
async def test_two_concurrent_duplicate_requests_create_one_event_and_job(
    concurrent_harness: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    client, factory = concurrent_harness
    body = webhook_body()
    signature = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-Razorpay-Event-Id": "event_concurrent_001",
        "X-Razorpay-Signature": signature,
    }

    first, second = await asyncio.gather(
        client.post("/webhooks/razorpay", content=body, headers=headers),
        client.post("/webhooks/razorpay", content=body, headers=headers),
    )
    async with factory() as session:
        event_count = await session.scalar(select(func.count()).select_from(WebhookEvent))
        job_count = await session.scalar(select(func.count()).select_from(ProcessingJob))

    assert first.status_code == 200
    assert second.status_code == 200
    assert {first.json()["status"], second.json()["status"]} == {"accepted", "duplicate"}
    assert event_count == 1
    assert job_count == 1


@pytest.mark.asyncio
async def test_one_merchant_cannot_read_another_merchants_event(
    concurrent_harness: tuple[httpx.AsyncClient, async_sessionmaker[AsyncSession]],
) -> None:
    _, factory = concurrent_harness
    async with factory() as session:
        async with session.begin():
            event = (
                await WebhookRepository(session).insert_once(
                    NewWebhookEvent(
                        correlation_id=CORRELATION_ID,
                        merchant_id=MERCHANT_A,
                        razorpay_event_id="event_tenant_001",
                        razorpay_account_id=ACCOUNT_ID,
                        event_type="payment.failed",
                        provider_event_at=datetime.now(UTC),
                        payload_hash="a" * 64,
                        payload={"event": "payment.failed", "created_at": 1787664087},
                    )
                )
            ).event

        own_event = await WebhookRepository(session).get_for_merchant(MERCHANT_A, event.id)
        foreign_event = await WebhookRepository(session).get_for_merchant(MERCHANT_B, event.id)

    assert own_event is not None
    assert foreign_event is None
