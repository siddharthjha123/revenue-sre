"""Crash-recoverable worker orchestration for accepted webhook jobs."""

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from enum import StrEnum
from time import perf_counter
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..database.models.webhook_event import WebhookEvent
from ..database.repositories.processing_job_repository import (
    ClaimedJob,
    JobLeaseLostError,
    ProcessingJobRepository,
)
from ..database.repositories.webhook_repository import WebhookRepository
from ..observability.metrics import (
    WEBHOOK_PROCESSING_DURATION_SECONDS,
    WEBHOOK_PROCESSING_FAILURES_TOTAL,
)
from ..services.event_normalizer import PaymentNormalizationError
from ..services.payment_event_pipeline import PaymentEventPipeline

logger = logging.getLogger(__name__)

# A handler performs the business work inside the same transaction that marks
# the job successful. Payment normalization is the default implementation.
WebhookJobHandler = Callable[[WebhookEvent, AsyncSession], Awaitable[None]]


class JobProcessingError(RuntimeError):
    """A classified processing failure safe to persist without customer data."""

    def __init__(self, *, code: str, safe_message: str, retryable: bool) -> None:
        super().__init__(safe_message)
        self.code = code
        self.safe_message = safe_message
        self.retryable = retryable


class WorkerOutcome(StrEnum):
    """Observable result of one polling iteration."""

    NO_JOB = "no_job"
    SUCCEEDED = "succeeded"
    RETRY_SCHEDULED = "retry_scheduled"
    DEAD_LETTERED = "dead_lettered"
    LEASE_LOST = "lease_lost"


@dataclass(frozen=True, slots=True)
class WorkerResult:
    """Small, non-sensitive result suitable for metrics and structured logs."""

    outcome: WorkerOutcome
    job_id: UUID | None = None
    attempt_count: int = 0
    error_code: str | None = None


class WebhookJobWorker:
    """Claim and process one PostgreSQL job at a time.

    The claim transaction is intentionally short. Processing occurs in a new
    transaction and must present the random lease token, preventing a stale
    worker from completing a job another worker has reclaimed.
    """

    def __init__(
        self,
        *,
        session_factory: async_sessionmaker[AsyncSession],
        handler: WebhookJobHandler | None = None,
        worker_id: str,
        lease_seconds: int = 60,
        retry_base_seconds: int = 5,
        retry_cap_seconds: int = 300,
    ) -> None:
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if lease_seconds <= 0 or retry_base_seconds <= 0 or retry_cap_seconds <= 0:
            raise ValueError("worker timing values must be positive")
        self._session_factory = session_factory
        # Payment normalization is the production default. Tests and future
        # job types can still inject another handler explicitly.
        self._handler = handler or PaymentEventPipeline()
        self._worker_id = worker_id[:128]
        self._lease_seconds = lease_seconds
        self._retry_base_seconds = retry_base_seconds
        self._retry_cap_seconds = retry_cap_seconds

    async def run_once(self) -> WorkerResult:
        """Claim at most one job, process it, and persist the outcome."""

        claim = await self._claim_one()
        if claim is None:
            return WorkerResult(outcome=WorkerOutcome.NO_JOB)

        started_at = perf_counter()
        try:
            await self._process_claim(claim)
        except JobLeaseLostError:
            result = WorkerResult(
                outcome=WorkerOutcome.LEASE_LOST,
                job_id=claim.job_id,
                attempt_count=claim.attempt_count,
                error_code="job_lease_lost",
            )
        except JobProcessingError as error:
            result = await self._record_failure(claim, error)
        except PaymentNormalizationError as error:
            result = await self._record_failure(
                claim,
                JobProcessingError(
                    code=error.code,
                    safe_message=error.safe_message,
                    retryable=False,
                ),
            )
        except Exception:
            # Never persist raw exception text: provider payload fragments or
            # customer data could otherwise leak into the queue table.
            result = await self._record_failure(
                claim,
                JobProcessingError(
                    code="unexpected_processing_error",
                    safe_message="Unexpected webhook processing failure",
                    retryable=True,
                ),
            )
        else:
            result = WorkerResult(
                outcome=WorkerOutcome.SUCCEEDED,
                job_id=claim.job_id,
                attempt_count=claim.attempt_count,
            )

        await self._record_observability(claim, result, started_at)
        return result

    async def _claim_one(self) -> ClaimedJob | None:
        async with self._session_factory() as session:
            async with session.begin():
                return await ProcessingJobRepository(session).claim_next(
                    worker_id=self._worker_id,
                    lease_seconds=self._lease_seconds,
                )

    async def _process_claim(self, claim: ClaimedJob) -> None:
        async with self._session_factory() as session:
            async with session.begin():
                job_repository = ProcessingJobRepository(session)
                job = await job_repository.get_active_lease_for_update(claim)
                event = await WebhookRepository(session).get_for_merchant(
                    claim.merchant_id,
                    claim.webhook_event_id,
                )
                if event is None:
                    raise JobProcessingError(
                        code="webhook_event_missing",
                        safe_message="Webhook event is missing for processing job",
                        retryable=False,
                    )

                await self._handler(event, session)
                await WebhookRepository(session).mark_processed(event)
                await job_repository.mark_succeeded(job)

    async def _record_failure(
        self,
        claim: ClaimedJob,
        error: JobProcessingError,
    ) -> WorkerResult:
        retry_after = min(
            self._retry_base_seconds * (2 ** max(claim.attempt_count - 1, 0)),
            self._retry_cap_seconds,
        )
        try:
            async with self._session_factory() as session:
                async with session.begin():
                    job_repository = ProcessingJobRepository(session)
                    job = await job_repository.get_active_lease_for_update(claim)
                    retrying = await job_repository.mark_failed(
                        job,
                        error_code=error.code,
                        safe_message=error.safe_message,
                        retryable=error.retryable,
                        retry_after_seconds=retry_after,
                    )
                    if not retrying:
                        event = await WebhookRepository(session).get_for_merchant(
                            claim.merchant_id,
                            claim.webhook_event_id,
                        )
                        if event is not None:
                            await WebhookRepository(session).mark_failed(
                                event,
                                reason=error.safe_message,
                            )
        except JobLeaseLostError:
            return WorkerResult(
                outcome=WorkerOutcome.LEASE_LOST,
                job_id=claim.job_id,
                attempt_count=claim.attempt_count,
                error_code="job_lease_lost",
            )

        return WorkerResult(
            outcome=(WorkerOutcome.RETRY_SCHEDULED if retrying else WorkerOutcome.DEAD_LETTERED),
            job_id=claim.job_id,
            attempt_count=claim.attempt_count,
            error_code=error.code,
        )

    async def _record_observability(
        self,
        claim: ClaimedJob,
        result: WorkerResult,
        started_at: float,
    ) -> None:
        """Record safe metrics/log fields without affecting job correctness."""

        duration_seconds = perf_counter() - started_at
        WEBHOOK_PROCESSING_DURATION_SECONDS.observe(duration_seconds)
        if result.outcome not in {WorkerOutcome.SUCCEEDED, WorkerOutcome.NO_JOB}:
            WEBHOOK_PROCESSING_FAILURES_TOTAL.inc()

        event = None
        try:
            async with self._session_factory() as session:
                event = await WebhookRepository(session).get_for_merchant(
                    claim.merchant_id,
                    claim.webhook_event_id,
                )
        except Exception:
            # Observability must never cause a successfully persisted job to be
            # retried, and raw database exceptions may include unsafe values.
            event = None

        logger.log(
            logging.INFO if result.outcome == WorkerOutcome.SUCCEEDED else logging.WARNING,
            "Webhook job processing completed",
            extra={
                "correlation_id": str(event.correlation_id) if event is not None else None,
                "razorpay_event_id": event.razorpay_event_id if event is not None else None,
                "merchant_id": str(claim.merchant_id),
                "event_type": event.event_type if event is not None else None,
                "processing_status": result.outcome.value,
                "duration_ms": round(duration_seconds * 1000, 3),
                "job_id": str(claim.job_id),
                "attempt_count": claim.attempt_count,
                "error_code": result.error_code,
            },
        )
