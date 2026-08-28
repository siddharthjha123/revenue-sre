"""Persistence operations for the PostgreSQL durable job queue."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import Select, insert, or_, select
from sqlalchemy.dialects.postgresql import insert as postgresql_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from ..models.processing_job import ProcessingJob, ProcessingJobStatus


class ProcessingJobTenantMismatchError(RuntimeError):
    """Raised when an event's existing job belongs to another merchant."""


class JobLeaseLostError(RuntimeError):
    """Raised when a stale worker tries to change a reclaimed job."""


@dataclass(frozen=True, slots=True)
class ProcessingJobInsertResult:
    """Result of idempotently creating a processing job."""

    job: ProcessingJob
    created: bool


@dataclass(frozen=True, slots=True)
class ClaimedJob:
    """Lease identity passed between the claim and processing transactions."""

    job_id: UUID
    webhook_event_id: UUID
    merchant_id: UUID
    lease_token: UUID
    attempt_count: int


class ProcessingJobRepository:
    """Queue access; callers own commit and rollback boundaries."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def enqueue_once(
        self,
        *,
        merchant_id: UUID,
        webhook_event_id: UUID,
        max_attempts: int = 5,
        priority: int = 100,
    ) -> ProcessingJobInsertResult:
        """Create exactly one job per webhook event, even under duplicate delivery."""

        job_id = uuid4()
        job_values = {
            "id": job_id,
            "merchant_id": merchant_id,
            "webhook_event_id": webhook_event_id,
            "max_attempts": max_attempts,
            "priority": priority,
            "status": ProcessingJobStatus.PENDING,
            "job_type": "process_webhook",
        }
        dialect = self._session.get_bind().dialect.name
        if dialect == "postgresql":
            statement = postgresql_insert(ProcessingJob)
        elif dialect == "sqlite":
            statement = sqlite_insert(ProcessingJob)
        else:
            statement = insert(ProcessingJob)

        if dialect in {"postgresql", "sqlite"}:
            statement = statement.on_conflict_do_nothing(
                index_elements=[ProcessingJob.webhook_event_id]
            )
        result = await self._session.execute(
            statement.values(**job_values).returning(ProcessingJob.id)
        )
        inserted_id = result.scalar_one_or_none()
        existing = await self.get_by_webhook_event_id(webhook_event_id)
        if existing is None:
            raise RuntimeError("Processing job insert did not return or resolve a job")
        if existing.merchant_id != merchant_id:
            raise ProcessingJobTenantMismatchError(
                "Webhook event job already belongs to another merchant"
            )
        return ProcessingJobInsertResult(job=existing, created=inserted_id is not None)

    async def get_by_webhook_event_id(self, webhook_event_id: UUID) -> ProcessingJob | None:
        """Find the unique job created for an inbox event."""

        return await self._session.scalar(
            select(ProcessingJob).where(ProcessingJob.webhook_event_id == webhook_event_id)
        )

    async def claim_next(self, *, worker_id: str, lease_seconds: int = 60) -> ClaimedJob | None:
        """Atomically lease one available job without blocking other workers."""

        now = datetime.now(UTC)
        statement: Select[tuple[ProcessingJob]] = (
            select(ProcessingJob)
            .where(
                ProcessingJob.attempt_count < ProcessingJob.max_attempts,
                or_(
                    ProcessingJob.status == ProcessingJobStatus.PENDING,
                    (
                        (ProcessingJob.status == ProcessingJobStatus.RETRY_SCHEDULED)
                        & (ProcessingJob.available_at <= now)
                    ),
                    (
                        (ProcessingJob.status == ProcessingJobStatus.PROCESSING)
                        & (ProcessingJob.lease_expires_at <= now)
                    ),
                ),
            )
            .order_by(
                ProcessingJob.priority.desc(),
                ProcessingJob.available_at,
                ProcessingJob.created_at,
            )
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        job = await self._session.scalar(statement)
        if job is None:
            return None

        lease_token = uuid4()
        job.status = ProcessingJobStatus.PROCESSING
        job.attempt_count += 1
        job.locked_by = worker_id[:128]
        job.lease_token = lease_token
        job.lease_expires_at = now + timedelta(seconds=lease_seconds)
        job.last_error_code = None
        job.last_error_message = None
        await self._session.flush()
        return ClaimedJob(
            job_id=job.id,
            webhook_event_id=job.webhook_event_id,
            merchant_id=job.merchant_id,
            lease_token=lease_token,
            attempt_count=job.attempt_count,
        )

    async def get_active_lease_for_update(
        self,
        claim: ClaimedJob,
        *,
        now: datetime | None = None,
    ) -> ProcessingJob:
        """Lock a job and prove this worker still owns its unexpired lease."""

        checked_at = now or datetime.now(UTC)
        job = await self._session.scalar(
            select(ProcessingJob)
            .where(
                ProcessingJob.id == claim.job_id,
                ProcessingJob.merchant_id == claim.merchant_id,
                ProcessingJob.status == ProcessingJobStatus.PROCESSING,
                ProcessingJob.lease_token == claim.lease_token,
                ProcessingJob.lease_expires_at > checked_at,
            )
            .with_for_update()
        )
        if job is None:
            raise JobLeaseLostError("Processing job lease is no longer owned by this worker")
        return job

    async def mark_succeeded(self, job: ProcessingJob) -> None:
        """Finish a job and clear its lease after processing commits."""

        now = datetime.now(UTC)
        job.status = ProcessingJobStatus.SUCCEEDED
        job.completed_at = now
        job.updated_at = now
        self._clear_lease(job)
        await self._session.flush()

    async def mark_failed(
        self,
        job: ProcessingJob,
        *,
        error_code: str,
        safe_message: str,
        retryable: bool,
        retry_after_seconds: int,
    ) -> bool:
        """Schedule a retry or dead-letter the job; return whether it will retry."""

        now = datetime.now(UTC)
        should_retry = retryable and job.attempt_count < job.max_attempts
        job.last_error_code = error_code[:128]
        job.last_error_message = safe_message[:1000]
        job.updated_at = now
        self._clear_lease(job)
        if should_retry:
            job.status = ProcessingJobStatus.RETRY_SCHEDULED
            job.available_at = now + timedelta(seconds=retry_after_seconds)
        else:
            job.status = ProcessingJobStatus.DEAD_LETTER
            job.completed_at = now
        await self._session.flush()
        return should_retry

    async def renew_lease(
        self,
        claim: ClaimedJob,
        *,
        lease_seconds: int,
    ) -> None:
        """Extend a lease while retaining the same ownership token."""

        job = await self.get_active_lease_for_update(claim)
        job.lease_expires_at = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        await self._session.flush()

    @staticmethod
    def _clear_lease(job: ProcessingJob) -> None:
        job.locked_by = None
        job.lease_token = None
        job.lease_expires_at = None
