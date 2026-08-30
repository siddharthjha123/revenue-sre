"""Load one incident through the authenticated production webhook pipeline.

This utility never inserts incidents or evidence directly. It emits a small,
clearly-labelled set of Razorpay-format demo webhooks through the running API,
then waits for the durable worker and detector to create an incident.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import SecretStr
from sqlalchemy import text

from backend.app.config import Settings, get_settings
from backend.app.database.base import create_database_engine


class DemoLoadError(RuntimeError):
    """Safe operator-facing failure without secret or payload disclosure."""


@dataclass(frozen=True, slots=True)
class DemoEvent:
    """One deterministic provider event in the baseline/current timeline."""

    status: str
    occurred_at: datetime
    payment_id: str
    order_id: str
    amount_subunits: int


def build_demo_events(anchor: datetime, run_id: str) -> list[DemoEvent]:
    """Build five healthy baseline and five current-window attempts.

    The current window contains two captured and three failed UPI payments,
    producing a 60% failure rate and 60,000 paise at risk under demo settings.
    """

    timeline = [
        ("captured", -20, 5_000),
        ("captured", -19, 5_000),
        ("captured", -18, 5_000),
        ("captured", -17, 5_000),
        ("captured", -16, 5_000),
        ("captured", -4, 7_000),
        ("captured", -3, 8_000),
        ("failed", -2, 10_000),
        ("failed", -1, 20_000),
        ("failed", 0, 30_000),
    ]
    return [
        DemoEvent(
            status=status,
            occurred_at=anchor + timedelta(minutes=minutes),
            payment_id=f"pay_Demo{run_id}{index:02d}",
            order_id=f"order_Demo{run_id}{index:02d}",
            amount_subunits=amount,
        )
        for index, (status, minutes, amount) in enumerate(timeline)
    ]


def event_payload(event: DemoEvent, account_id: str) -> dict[str, Any]:
    """Create a PII-free Razorpay payment webhook envelope."""

    event_type = f"payment.{event.status}"
    timestamp = int(event.occurred_at.timestamp())
    failed = event.status == "failed"
    return {
        "entity": "event",
        "account_id": account_id,
        "event": event_type,
        "contains": ["payment"],
        "payload": {
            "payment": {
                "entity": {
                    "id": event.payment_id,
                    "entity": "payment",
                    "amount": event.amount_subunits,
                    "currency": "INR",
                    "status": event.status,
                    "order_id": event.order_id,
                    "method": "upi",
                    "captured": event.status == "captured",
                    "international": False,
                    "bank": "HDFC",
                    "error_code": "BAD_REQUEST_ERROR" if failed else None,
                    "error_description": "Payment timed out at bank" if failed else None,
                    "error_source": "bank" if failed else None,
                    "error_step": "payment_authorization" if failed else None,
                    "error_reason": "payment_timed_out" if failed else None,
                    "created_at": timestamp,
                }
            }
        },
        "created_at": timestamp,
    }


def sign(raw_body: bytes, secret: SecretStr) -> str:
    """Sign the exact bytes sent to the webhook endpoint."""

    return hmac.new(
        secret.get_secret_value().encode("utf-8"),
        raw_body,
        hashlib.sha256,
    ).hexdigest()


def load_demo_incident(
    *,
    settings: Settings,
    base_url: str,
    timeout_seconds: int,
    anchor: datetime | None = None,
) -> dict[str, Any]:
    """Submit the batch and wait until the owning merchant can retrieve it."""

    _validate_settings(settings)
    assert settings.razorpay_webhook_secret is not None
    assert settings.razorpay_account_id is not None
    assert settings.merchant_id is not None

    anchor = anchor or datetime.now(UTC)
    run_id = anchor.strftime("%Y%m%d%H%M%S")
    events = build_demo_events(anchor, run_id)
    accepted = 0
    for index, event in enumerate(events):
        payload = event_payload(event, settings.razorpay_account_id)
        raw_body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        response = _request_json(
            f"{base_url}/webhooks/razorpay",
            method="POST",
            body=raw_body,
            headers={
                "Content-Type": "application/json",
                "X-Razorpay-Event-Id": f"event_demo_{run_id}_{index:02d}",
                "X-Razorpay-Signature": sign(
                    raw_body,
                    settings.razorpay_webhook_secret,
                ),
            },
        )
        if response.get("status") not in {"accepted", "duplicate"}:
            raise DemoLoadError("Webhook endpoint returned an unexpected status")
        accepted += response.get("status") == "accepted"

    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        incidents = _request_json(
            f"{base_url}/incidents?limit=20",
            headers={"X-Merchant-Id": str(settings.merchant_id)},
        )
        matching = [
            incident
            for incident in incidents
            if incident.get("method") == "upi"
            and incident.get("bank") == "HDFC"
            and incident.get("error_reason") == "payment_timed_out"
        ]
        if matching:
            incident = matching[0]
            return {
                "run_id": run_id,
                "accepted_webhooks": accepted,
                "incident_id": incident["incident_id"],
                "current_failure_rate": incident["current_failure_rate"],
                "revenue_at_risk_subunits": incident["revenue_at_risk_subunits"],
            }
        time.sleep(1)
    raise DemoLoadError(
        "Timed out waiting for the worker to create an incident; inspect worker logs"
    )


def _validate_settings(settings: Settings) -> None:
    missing = []
    if settings.merchant_id is None:
        missing.append("MERCHANT_ID")
    if settings.razorpay_account_id is None:
        missing.append("RAZORPAY_ACCOUNT_ID")
    if settings.razorpay_webhook_secret is None:
        missing.append("RAZORPAY_WEBHOOK_SECRET")
    if missing:
        raise DemoLoadError(f"Missing required settings: {', '.join(missing)}")


async def database_clock(database_url: str) -> datetime:
    """Read the detector's clock source to tolerate local Docker clock drift.

    Production hosts should synchronize their clocks. Docker Desktop can drift
    after Windows sleeps, however, and a demo event generated from the host
    clock may then fall outside the worker's strict five-minute window.
    """

    database_engine = create_database_engine(database_url)
    try:
        async with database_engine.connect() as connection:
            value = await connection.scalar(text("SELECT CURRENT_TIMESTAMP"))
    finally:
        await database_engine.dispose()
    if not isinstance(value, datetime):
        raise DemoLoadError("Database did not return a valid current timestamp")
    return value.astimezone(UTC)


def _request_json(
    url: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
) -> Any:
    request = Request(url, data=body, headers=headers or {}, method=method)
    try:
        with urlopen(request, timeout=5) as response:  # noqa: S310 - local operator URL
            return json.loads(response.read())
    except HTTPError as error:
        safe_body = error.read().decode("utf-8", errors="replace")[:500]
        raise DemoLoadError(f"HTTP {error.code} from local API: {safe_body}") from error
    except URLError as error:
        raise DemoLoadError("Could not reach the local Revenue SRE API") from error


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Create one persistent incident through signed demo webhooks."
    )
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--timeout-seconds", type=int, default=30)
    args = parser.parse_args()
    try:
        result = load_demo_incident(
            settings=get_settings(),
            base_url=args.base_url.rstrip("/"),
            timeout_seconds=max(args.timeout_seconds, 1),
            anchor=asyncio.run(database_clock(get_settings().database_url)),
        )
    except DemoLoadError as error:
        raise SystemExit(f"Demo incident load failed: {error}") from error
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
