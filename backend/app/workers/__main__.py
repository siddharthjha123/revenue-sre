"""Command-line entrypoint for the independently deployed webhook worker."""

import asyncio
import logging
import signal
from collections.abc import Callable
from pathlib import Path

from prometheus_client import start_http_server

from ..config import get_settings
from ..database.base import AsyncSessionFactory, dispose_database_engine
from ..observability.logging import configure_structured_logging
from .runtime import WorkerRuntime, generate_worker_id, verify_database_ready
from .webhook_worker import WebhookJobWorker

logger = logging.getLogger(__name__)


def _install_signal_handlers(callback: Callable[[str], None]) -> Callable[[], None]:
    """Install portable SIGINT/SIGTERM handlers and return a restoration callback."""

    previous_handlers: dict[signal.Signals, object] = {}

    def handler(signum: int, frame: object) -> None:
        del frame
        callback(signal.Signals(signum).name.lower())

    for supported_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            previous_handlers[supported_signal] = signal.getsignal(supported_signal)
            signal.signal(supported_signal, handler)
        except (OSError, ValueError):
            continue

    def restore() -> None:
        for supported_signal, previous in previous_handlers.items():
            signal.signal(supported_signal, previous)

    return restore


async def run_worker_process() -> int:
    """Validate dependencies, run until signalled, and release all resources."""

    settings = get_settings()
    configure_structured_logging(settings.log_level)
    worker_id = generate_worker_id(settings.worker_id)

    if settings.worker_health_max_staleness_seconds <= settings.worker_heartbeat_interval_seconds:
        logger.error(
            "Worker health configuration is invalid",
            extra={
                "worker_id": worker_id,
                "worker_status": "startup_failed",
                "error_code": "invalid_worker_health_configuration",
            },
        )
        return 2

    try:
        await verify_database_ready(AsyncSessionFactory)
    except Exception:
        logger.error(
            "Worker database readiness check failed",
            extra={
                "worker_id": worker_id,
                "worker_status": "startup_failed",
                "error_code": "database_not_ready",
            },
        )
        await dispose_database_engine()
        return 1

    metrics_server = None
    if settings.worker_metrics_port:
        try:
            metrics_server, _ = start_http_server(
                port=settings.worker_metrics_port,
                addr=settings.worker_metrics_host,
            )
        except OSError:
            logger.error(
                "Worker metrics server failed to start",
                extra={
                    "worker_id": worker_id,
                    "worker_status": "startup_failed",
                    "error_code": "metrics_server_unavailable",
                },
            )
            await dispose_database_engine()
            return 1

    runtime = WorkerRuntime(
        worker=WebhookJobWorker(
            session_factory=AsyncSessionFactory,
            worker_id=worker_id,
            lease_seconds=settings.worker_lease_seconds,
            retry_base_seconds=settings.worker_retry_base_seconds,
            retry_cap_seconds=settings.worker_retry_cap_seconds,
        ),
        worker_id=worker_id,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
        loop_error_backoff_seconds=settings.worker_loop_error_backoff_seconds,
        heartbeat_interval_seconds=settings.worker_heartbeat_interval_seconds,
        heartbeat_path=Path(settings.worker_heartbeat_path),
    )
    current_task = asyncio.current_task()
    force_shutdown_handle: asyncio.TimerHandle | None = None

    def request_shutdown(reason: str) -> None:
        nonlocal force_shutdown_handle
        runtime.request_shutdown(reason)
        if current_task is not None and force_shutdown_handle is None:
            force_shutdown_handle = asyncio.get_running_loop().call_later(
                settings.worker_shutdown_timeout_seconds,
                current_task.cancel,
            )

    restore_signals = _install_signal_handlers(request_shutdown)
    exit_code = 0
    try:
        await runtime.run()
    except asyncio.CancelledError:
        if not runtime.shutdown_requested:
            raise
        exit_code = 1
        logger.error(
            "Worker exceeded graceful shutdown timeout",
            extra={
                "worker_id": worker_id,
                "worker_status": "forced_stop",
                "error_code": "worker_shutdown_timeout",
            },
        )
    finally:
        if force_shutdown_handle is not None:
            force_shutdown_handle.cancel()
        restore_signals()
        if metrics_server is not None:
            metrics_server.shutdown()
            metrics_server.server_close()
        await dispose_database_engine()
    return exit_code


def main() -> None:
    """Run the worker process and expose failures through its exit status."""

    raise SystemExit(asyncio.run(run_worker_process()))


if __name__ == "__main__":
    main()
