"""PostgreSQL-backed work queue for durable webhook processing."""

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    String,
    Uuid,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base


class ProcessingJobStatus(StrEnum):
    """Lifecycle states for a durable processing job."""

    PENDING = "pending"
    PROCESSING = "processing"
    RETRY_SCHEDULED = "retry_scheduled"
    SUCCEEDED = "succeeded"
    DEAD_LETTER = "dead_letter"


class ProcessingJob(Base):
    """A retryable, leased unit of work created with a webhook event."""

    __tablename__ = "processing_jobs"
    __table_args__ = (
        CheckConstraint("attempt_count >= 0", name="ck_processing_jobs_attempt_count"),
        CheckConstraint("max_attempts > 0", name="ck_processing_jobs_max_attempts"),
        CheckConstraint("priority >= 0", name="ck_processing_jobs_priority"),
        CheckConstraint(
            "status IN ('pending', 'processing', 'retry_scheduled', 'succeeded', 'dead_letter')",
            name="processing_job_status",
        ),
        Index(
            "ix_processing_jobs_claim",
            "status",
            "available_at",
            "priority",
            "created_at",
        ),
    )

    id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid4)
    merchant_id: Mapped[UUID] = mapped_column(Uuid(as_uuid=True), nullable=False, index=True)
    webhook_event_id: Mapped[UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("webhook_events.id", ondelete="CASCADE"),
        unique=True,
        nullable=False,
    )
    job_type: Mapped[str] = mapped_column(
        String(64),
        default="process_webhook",
        server_default="process_webhook",
        nullable=False,
    )
    status: Mapped[ProcessingJobStatus] = mapped_column(
        Enum(
            ProcessingJobStatus,
            name="processing_job_status",
            native_enum=False,
            validate_strings=True,
            values_callable=lambda enum_type: [item.value for item in enum_type],
        ),
        default=ProcessingJobStatus.PENDING,
        server_default=ProcessingJobStatus.PENDING.value,
        nullable=False,
    )
    priority: Mapped[int] = mapped_column(
        Integer, default=100, server_default="100", nullable=False
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )
    max_attempts: Mapped[int] = mapped_column(
        Integer, default=5, server_default="5", nullable=False
    )
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    locked_by: Mapped[str | None] = mapped_column(String(128))
    lease_token: Mapped[UUID | None] = mapped_column(Uuid(as_uuid=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_code: Mapped[str | None] = mapped_column(String(128))
    last_error_message: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    webhook_event = relationship("WebhookEvent", lazy="raise")
