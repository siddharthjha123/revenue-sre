"""Authenticated, idempotent Razorpay webhook ingestion endpoint."""

import hashlib
import json
import logging
from datetime import UTC, datetime
from json import JSONDecodeError
from time import perf_counter
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import Settings, get_settings
from ..database.base import get_db_session
from ..database.repositories.processing_job_repository import ProcessingJobRepository
from ..database.repositories.webhook_repository import (
    NewWebhookEvent,
    WebhookRepository,
    WebhookTenantMismatchError,
)
from ..observability.context import get_correlation_id
from ..observability.metrics import (
    WEBHOOKS_DUPLICATE_TOTAL,
    WEBHOOKS_INVALID_SIGNATURE_TOTAL,
    WEBHOOKS_RECEIVED_TOTAL,
)
from ..schemas.webhook import (
    RazorpayWebhookEnvelope,
    SupportedWebhookEvent,
    WebhookIngestionResponse,
    WebhookIngestionStatus,
)
from ..services.webhook_payload import InvalidWebhookPayloadError, sanitize_webhook_payload
from ..services.webhook_signature import (
    InvalidSignatureError,
    WebhookConfigurationError,
    verify_razorpay_signature,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/webhooks", tags=["webhooks"])
SUPPORTED_EVENT_VALUES = frozenset(event.value for event in SupportedWebhookEvent)


@router.post(
    "/razorpay",
    response_model=WebhookIngestionResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate and persist a Razorpay webhook",
)
async def ingest_razorpay_webhook(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_db_session)],
    settings: Annotated[Settings, Depends(get_settings)],
    razorpay_signature: Annotated[
        str | None,
        Header(alias="X-Razorpay-Signature"),
    ] = None,
    razorpay_event_id: Annotated[
        str | None,
        Header(alias="X-Razorpay-Event-Id"),
    ] = None,
) -> WebhookIngestionResponse:
    """Verify raw bytes, bind the tenant, and durably store the event once.

    The inbox event and its processing job are committed in one transaction.
    No payment normalization or recovery action runs in the request process.
    """

    started_at = perf_counter()
    correlation_id = get_correlation_id()
    WEBHOOKS_RECEIVED_TOTAL.inc()
    raw_body = await request.body()
    if len(raw_body) > settings.webhook_max_body_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Webhook payload is too large",
        )

    if not razorpay_signature:
        WEBHOOKS_INVALID_SIGNATURE_TOTAL.inc()
        _log_ingestion(
            correlation_id=correlation_id,
            razorpay_event_id=razorpay_event_id,
            merchant_id=settings.merchant_id,
            event_type=None,
            processing_status="rejected_missing_signature",
            started_at=started_at,
            level=logging.WARNING,
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing webhook signature",
        )

    try:
        verify_razorpay_signature(
            raw_body=raw_body,
            received_signature=razorpay_signature,
            webhook_secret=settings.razorpay_webhook_secret,
        )
    except WebhookConfigurationError as error:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook authentication is unavailable",
        ) from error
    except InvalidSignatureError as error:
        WEBHOOKS_INVALID_SIGNATURE_TOTAL.inc()
        _log_ingestion(
            correlation_id=correlation_id,
            razorpay_event_id=razorpay_event_id,
            merchant_id=settings.merchant_id,
            event_type=None,
            processing_status="rejected_invalid_signature",
            started_at=started_at,
            level=logging.WARNING,
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid webhook signature",
        ) from error

    if not razorpay_event_id or len(razorpay_event_id) > 255:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Missing or invalid Razorpay event ID",
        )

    try:
        decoded_payload = json.loads(raw_body)
    except (JSONDecodeError, UnicodeDecodeError) as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Malformed webhook JSON",
        ) from error

    if not isinstance(decoded_payload, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook payload must be a JSON object",
        )

    try:
        envelope = RazorpayWebhookEnvelope.model_validate(decoded_payload)
    except ValidationError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid webhook envelope",
        ) from error

    if settings.merchant_id is None or settings.razorpay_account_id is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook tenant binding is unavailable",
        )
    if envelope.account_id != settings.razorpay_account_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Webhook account is not authorized",
        )

    try:
        sanitized_payload = sanitize_webhook_payload(envelope)
    except InvalidWebhookPayloadError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid payment webhook payload",
        ) from error

    event_supported = envelope.event in SUPPORTED_EVENT_VALUES
    webhook_repository = WebhookRepository(session)
    job_repository = ProcessingJobRepository(session)
    try:
        async with session.begin():
            insertion = await webhook_repository.insert_once(
                NewWebhookEvent(
                    correlation_id=correlation_id,
                    merchant_id=settings.merchant_id,
                    razorpay_event_id=razorpay_event_id,
                    razorpay_account_id=envelope.account_id,
                    event_type=envelope.event,
                    provider_event_at=datetime.fromtimestamp(envelope.created_at, UTC),
                    payload_hash=hashlib.sha256(raw_body).hexdigest(),
                    payload=sanitized_payload,
                )
            )
            if insertion.created:
                if event_supported:
                    await job_repository.enqueue_once(
                        merchant_id=settings.merchant_id,
                        webhook_event_id=insertion.event.id,
                        max_attempts=settings.worker_max_attempts,
                    )
                else:
                    await webhook_repository.mark_ignored(
                        insertion.event,
                        reason="Unsupported webhook event type",
                    )
    except WebhookTenantMismatchError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Webhook event identity conflict",
        ) from error
    except SQLAlchemyError as error:
        logger.error(
            "Failed to persist authenticated Razorpay webhook",
            extra={
                "correlation_id": str(correlation_id),
                "razorpay_event_id": razorpay_event_id,
                "merchant_id": str(settings.merchant_id),
                "event_type": envelope.event,
                "processing_status": "persistence_failed",
                "duration_ms": _duration_ms(started_at),
                "error_code": type(error).__name__,
            },
        )
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Webhook persistence is temporarily unavailable",
        ) from error

    if not insertion.created:
        ingestion_status = WebhookIngestionStatus.DUPLICATE
        WEBHOOKS_DUPLICATE_TOTAL.inc()
    elif not event_supported:
        ingestion_status = WebhookIngestionStatus.IGNORED
    else:
        ingestion_status = WebhookIngestionStatus.ACCEPTED

    _log_ingestion(
        correlation_id=correlation_id,
        razorpay_event_id=razorpay_event_id,
        merchant_id=settings.merchant_id,
        event_type=envelope.event,
        processing_status=ingestion_status.value,
        started_at=started_at,
    )
    return WebhookIngestionResponse(status=ingestion_status)


def _duration_ms(started_at: float) -> float:
    return round((perf_counter() - started_at) * 1000, 3)


def _log_ingestion(
    *,
    correlation_id: object,
    razorpay_event_id: str | None,
    merchant_id: object,
    event_type: str | None,
    processing_status: str,
    started_at: float,
    level: int = logging.INFO,
) -> None:
    """Emit only whitelisted identifiers and timing—never request content."""

    logger.log(
        level,
        "Razorpay webhook ingestion completed",
        extra={
            "correlation_id": str(correlation_id),
            "razorpay_event_id": razorpay_event_id,
            "merchant_id": str(merchant_id) if merchant_id is not None else None,
            "event_type": event_type,
            "processing_status": processing_status,
            "duration_ms": _duration_ms(started_at),
        },
    )
