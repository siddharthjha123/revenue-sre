"""Process-runtime tests for polling, health, recovery, and graceful shutdown."""

import asyncio
import os
from pathlib import Path
from time import time
from uuid import uuid4

import pytest
from prometheus_client import REGISTRY
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from backend.app.workers.runtime import (
    WorkerRuntime,
    generate_worker_id,
    heartbeat_is_fresh,
    verify_database_ready,
)
from backend.app.workers.webhook_worker import WorkerOutcome, WorkerResult


def heartbeat_path() -> Path:
    """Use a repository-local disposable heartbeat under restricted test runners."""

    return Path(__file__).parent / f".worker-heartbeat-{uuid4().hex}"


async def no_heartbeat(path: Path) -> None:
    """Avoid filesystem timing when a test is focused on polling behavior."""

    del path


@pytest.mark.asyncio
async def test_runtime_processes_jobs_until_shutdown() -> None:
    jobs_before = (
        REGISTRY.get_sample_value(
            "worker_jobs_processed_total",
            {"outcome": WorkerOutcome.SUCCEEDED.value},
        )
        or 0
    )
    runtime: WorkerRuntime

    class FakeWorker:
        calls = 0

        async def run_once(self) -> WorkerResult:
            self.calls += 1
            if self.calls == 2:
                runtime.request_shutdown("test_complete")
            return WorkerResult(outcome=WorkerOutcome.SUCCEEDED)

    fake_worker = FakeWorker()
    runtime = WorkerRuntime(
        worker=fake_worker,
        worker_id="runtime-test",
        poll_interval_seconds=1,
        loop_error_backoff_seconds=1,
        heartbeat_interval_seconds=1,
        heartbeat_path=heartbeat_path(),
        heartbeat_writer=no_heartbeat,
    )

    await runtime.run()

    assert fake_worker.calls == 2
    assert (
        REGISTRY.get_sample_value(
            "worker_jobs_processed_total",
            {"outcome": WorkerOutcome.SUCCEEDED.value},
        )
        == jobs_before + 2
    )


@pytest.mark.asyncio
async def test_empty_queue_waits_instead_of_busy_polling() -> None:
    first_poll = asyncio.Event()

    class EmptyWorker:
        calls = 0

        async def run_once(self) -> WorkerResult:
            self.calls += 1
            first_poll.set()
            return WorkerResult(outcome=WorkerOutcome.NO_JOB)

    worker = EmptyWorker()
    runtime = WorkerRuntime(
        worker=worker,
        worker_id="idle-test",
        poll_interval_seconds=10,
        loop_error_backoff_seconds=1,
        heartbeat_interval_seconds=1,
        heartbeat_path=heartbeat_path(),
        heartbeat_writer=no_heartbeat,
    )
    runtime_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(first_poll.wait(), timeout=1)
    await asyncio.sleep(0.02)

    assert worker.calls == 1

    runtime.request_shutdown("test_complete")
    await asyncio.wait_for(runtime_task, timeout=1)


@pytest.mark.asyncio
async def test_shutdown_finishes_active_job_without_claiming_another() -> None:
    processing_started = asyncio.Event()
    allow_completion = asyncio.Event()

    class SlowWorker:
        calls = 0

        async def run_once(self) -> WorkerResult:
            self.calls += 1
            processing_started.set()
            await allow_completion.wait()
            return WorkerResult(outcome=WorkerOutcome.SUCCEEDED)

    worker = SlowWorker()
    runtime = WorkerRuntime(
        worker=worker,
        worker_id="graceful-test",
        poll_interval_seconds=1,
        loop_error_backoff_seconds=1,
        heartbeat_interval_seconds=1,
        heartbeat_path=heartbeat_path(),
        heartbeat_writer=no_heartbeat,
    )
    runtime_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(processing_started.wait(), timeout=1)

    runtime.request_shutdown("sigterm")
    await asyncio.sleep(0)
    assert not runtime_task.done()

    allow_completion.set()
    await asyncio.wait_for(runtime_task, timeout=1)
    assert worker.calls == 1


@pytest.mark.asyncio
async def test_runtime_recovers_from_polling_error_without_logging_exception_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    runtime: WorkerRuntime

    class RecoveringWorker:
        calls = 0

        async def run_once(self) -> WorkerResult:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("postgresql://user:secret@example.invalid/customer@example.com")
            runtime.request_shutdown("recovered")
            return WorkerResult(outcome=WorkerOutcome.NO_JOB)

    worker = RecoveringWorker()
    runtime = WorkerRuntime(
        worker=worker,
        worker_id="recovery-test",
        poll_interval_seconds=1,
        loop_error_backoff_seconds=0.001,
        heartbeat_interval_seconds=1,
        heartbeat_path=heartbeat_path(),
        heartbeat_writer=no_heartbeat,
    )

    with caplog.at_level("ERROR"):
        await runtime.run()

    assert worker.calls == 2
    rendered_logs = " ".join(record.getMessage() for record in caplog.records)
    assert "secret" not in rendered_logs
    assert "customer@example.com" not in rendered_logs
    assert any(record.error_code == "worker_loop_error" for record in caplog.records)


@pytest.mark.asyncio
async def test_heartbeat_continues_while_job_is_running() -> None:
    processing_started = asyncio.Event()
    allow_completion = asyncio.Event()
    heartbeat_count = 0
    runtime: WorkerRuntime

    async def count_heartbeat(path: Path) -> None:
        nonlocal heartbeat_count
        del path
        heartbeat_count += 1

    class SlowWorker:
        async def run_once(self) -> WorkerResult:
            processing_started.set()
            await allow_completion.wait()
            runtime.request_shutdown("test_complete")
            return WorkerResult(outcome=WorkerOutcome.SUCCEEDED)

    runtime = WorkerRuntime(
        worker=SlowWorker(),
        worker_id="heartbeat-test",
        poll_interval_seconds=1,
        loop_error_backoff_seconds=1,
        heartbeat_interval_seconds=0.005,
        heartbeat_path=heartbeat_path(),
        heartbeat_writer=count_heartbeat,
    )
    runtime_task = asyncio.create_task(runtime.run())
    await asyncio.wait_for(processing_started.wait(), timeout=1)
    await asyncio.sleep(0.025)
    allow_completion.set()
    await asyncio.wait_for(runtime_task, timeout=1)

    assert heartbeat_count >= 2


@pytest.mark.asyncio
async def test_database_readiness_query() -> None:
    engine = create_async_engine("sqlite+aiosqlite://")
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        await verify_database_ready(factory)
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_heartbeat_freshness_and_runtime_cleanup() -> None:
    path = heartbeat_path()

    class EmptyWorker:
        async def run_once(self) -> WorkerResult:
            return WorkerResult(outcome=WorkerOutcome.NO_JOB)

    runtime = WorkerRuntime(
        worker=EmptyWorker(),
        worker_id="health-test",
        poll_interval_seconds=10,
        loop_error_backoff_seconds=1,
        heartbeat_interval_seconds=0.005,
        heartbeat_path=path,
    )
    runtime_task = asyncio.create_task(runtime.run())
    for _ in range(100):
        if await heartbeat_is_fresh(path, max_staleness_seconds=1):
            break
        await asyncio.sleep(0.005)

    assert await heartbeat_is_fresh(path, max_staleness_seconds=1)

    runtime.request_shutdown("test_complete")
    await asyncio.wait_for(runtime_task, timeout=1)
    assert not path.exists()


@pytest.mark.asyncio
async def test_stale_heartbeat_is_unhealthy() -> None:
    path = heartbeat_path()
    path.touch()
    os.utime(path, (time() - 60, time() - 60))
    try:
        assert not await heartbeat_is_fresh(path, max_staleness_seconds=10)
    finally:
        path.unlink(missing_ok=True)


def test_worker_id_generation_and_configured_override() -> None:
    generated = generate_worker_id()

    assert generated
    assert str(os.getpid()) in generated
    assert len(generated) <= 128
    assert generate_worker_id("  merchant-worker-a  ") == "merchant-worker-a"
