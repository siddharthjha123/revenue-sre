"""Continuous, signal-aware runtime around the durable webhook job worker."""

import asyncio
import logging
import os
import platform
from collections.abc import Awaitable, Callable
from contextlib import suppress
from pathlib import Path
from time import time
from typing import Protocol
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..observability.metrics import (
    WORKER_JOBS_PROCESSED_TOTAL,
    WORKER_LAST_SUCCESS_TIMESTAMP_SECONDS,
    WORKER_LOOP_ERRORS_TOTAL,
    WORKER_POLLS_TOTAL,
    WORKER_RUNTIME_UP,
)
from .webhook_worker import WorkerOutcome, WorkerResult

logger = logging.getLogger(__name__)


class Worker(Protocol):
    """Minimal worker operation required by the process runtime."""

    async def run_once(self) -> WorkerResult:
        """Claim and process at most one durable job."""


HeartbeatWriter = Callable[[Path], Awaitable[None]]


def generate_worker_id(configured_id: str | None = None) -> str:
    """Return a stable configured ID or a collision-resistant process ID."""

    if configured_id and configured_id.strip():
        return configured_id.strip()[:128]
    hostname = platform.node().strip() or "unknown-host"
    suffix = uuid4().hex[:8]
    return f"{hostname}-{os.getpid()}-{suffix}"[:128]


async def verify_database_ready(
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """Fail fast unless the worker can execute a lightweight database query."""

    async with session_factory() as session:
        await session.execute(text("SELECT 1"))


async def heartbeat_is_fresh(path: Path, *, max_staleness_seconds: float) -> bool:
    """Return whether the runtime heartbeat exists and is recent enough."""

    try:
        modified_at = await asyncio.to_thread(lambda: path.stat().st_mtime)
    except (FileNotFoundError, OSError):
        return False
    return time() - modified_at <= max_staleness_seconds


class WorkerRuntime:
    """Poll durable jobs until shutdown while remaining observable and responsive."""

    def __init__(
        self,
        *,
        worker: Worker,
        worker_id: str,
        poll_interval_seconds: float,
        loop_error_backoff_seconds: float,
        heartbeat_interval_seconds: float,
        heartbeat_path: Path,
        shutdown_event: asyncio.Event | None = None,
        heartbeat_writer: HeartbeatWriter | None = None,
    ) -> None:
        timing_values = (
            poll_interval_seconds,
            loop_error_backoff_seconds,
            heartbeat_interval_seconds,
        )
        if not worker_id.strip():
            raise ValueError("worker_id must not be empty")
        if any(value <= 0 for value in timing_values):
            raise ValueError("worker runtime timing values must be positive")
        self._worker = worker
        self.worker_id = worker_id[:128]
        self._poll_interval_seconds = poll_interval_seconds
        self._loop_error_backoff_seconds = loop_error_backoff_seconds
        self._heartbeat_interval_seconds = heartbeat_interval_seconds
        self._heartbeat_path = heartbeat_path
        self._shutdown_event = shutdown_event or asyncio.Event()
        self._heartbeat_writer = heartbeat_writer or self._write_heartbeat

    @property
    def shutdown_requested(self) -> bool:
        """Expose shutdown state for process coordination and tests."""

        return self._shutdown_event.is_set()

    def request_shutdown(self, reason: str = "requested") -> None:
        """Stop future claims; any active job is allowed to finish first."""

        if self._shutdown_event.is_set():
            return
        logger.info(
            "Worker shutdown requested",
            extra={
                "worker_id": self.worker_id,
                "worker_status": "stopping",
                "error_code": reason,
            },
        )
        self._shutdown_event.set()

    async def run(self) -> None:
        """Run polling and heartbeat loops until a graceful shutdown is requested."""

        heartbeat_stop = asyncio.Event()
        heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(heartbeat_stop),
            name=f"worker-heartbeat-{self.worker_id}",
        )
        WORKER_RUNTIME_UP.set(1)
        logger.info(
            "Durable worker runtime started",
            extra={"worker_id": self.worker_id, "worker_status": "running"},
        )

        try:
            while not self._shutdown_event.is_set():
                try:
                    result = await self._worker.run_once()
                except asyncio.CancelledError:
                    raise
                except Exception:
                    # Runtime-level errors are deliberately generic because a
                    # database exception can contain connection credentials.
                    WORKER_LOOP_ERRORS_TOTAL.inc()
                    logger.error(
                        "Durable worker polling failed",
                        extra={
                            "worker_id": self.worker_id,
                            "worker_status": "degraded",
                            "error_code": "worker_loop_error",
                        },
                    )
                    await self._wait_or_shutdown(self._loop_error_backoff_seconds)
                    continue

                self._record_result(result)
                if result.outcome == WorkerOutcome.NO_JOB:
                    await self._wait_or_shutdown(self._poll_interval_seconds)
        finally:
            heartbeat_stop.set()
            await heartbeat_task
            await self._remove_heartbeat()
            WORKER_RUNTIME_UP.set(0)
            logger.info(
                "Durable worker runtime stopped",
                extra={"worker_id": self.worker_id, "worker_status": "stopped"},
            )

    def _record_result(self, result: WorkerResult) -> None:
        WORKER_POLLS_TOTAL.labels(outcome=result.outcome.value).inc()
        if result.outcome == WorkerOutcome.NO_JOB:
            return
        WORKER_JOBS_PROCESSED_TOTAL.labels(outcome=result.outcome.value).inc()
        if result.outcome == WorkerOutcome.SUCCEEDED:
            WORKER_LAST_SUCCESS_TIMESTAMP_SECONDS.set_to_current_time()

    async def _wait_or_shutdown(self, timeout_seconds: float) -> None:
        try:
            await asyncio.wait_for(self._shutdown_event.wait(), timeout=timeout_seconds)
        except TimeoutError:
            pass

    async def _heartbeat_loop(self, stop_event: asyncio.Event) -> None:
        while not stop_event.is_set():
            try:
                await self._heartbeat_writer(self._heartbeat_path)
            except asyncio.CancelledError:
                raise
            except Exception:
                WORKER_LOOP_ERRORS_TOTAL.inc()
                logger.error(
                    "Worker heartbeat update failed",
                    extra={
                        "worker_id": self.worker_id,
                        "worker_status": "degraded",
                        "error_code": "worker_heartbeat_error",
                    },
                )
            try:
                await asyncio.wait_for(
                    stop_event.wait(),
                    timeout=self._heartbeat_interval_seconds,
                )
            except TimeoutError:
                pass

    @staticmethod
    async def _write_heartbeat(path: Path) -> None:
        def write() -> None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()

        await asyncio.to_thread(write)

    async def _remove_heartbeat(self) -> None:
        def remove() -> None:
            with suppress(FileNotFoundError):
                self._heartbeat_path.unlink()

        await asyncio.to_thread(remove)
