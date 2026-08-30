"""Container health command for the independently running worker process."""

import asyncio
from pathlib import Path

from ..config import get_settings
from ..database.base import AsyncSessionFactory, dispose_database_engine
from .runtime import heartbeat_is_fresh, verify_database_ready


async def check_worker_health() -> bool:
    """Require both a recent runtime heartbeat and a reachable database."""

    settings = get_settings()
    if not await heartbeat_is_fresh(
        Path(settings.worker_heartbeat_path),
        max_staleness_seconds=settings.worker_health_max_staleness_seconds,
    ):
        return False
    try:
        await verify_database_ready(AsyncSessionFactory)
    except Exception:
        return False
    finally:
        await dispose_database_engine()
    return True


def main() -> None:
    """Exit zero only when the worker loop and its database are healthy."""

    healthy = asyncio.run(check_worker_health())
    print("worker healthy" if healthy else "worker unhealthy")
    raise SystemExit(0 if healthy else 1)


if __name__ == "__main__":
    main()
