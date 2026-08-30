"""Shared asynchronous SQLAlchemy engine and session lifecycle."""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from ..config import get_settings

settings = get_settings()


def create_database_engine(database_url: str):
    """Build an async engine with driver-appropriate connection settings."""

    options: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
    if database_url.startswith("postgresql+"):
        options.update(pool_size=10, max_overflow=20, pool_recycle=1800)

    return create_async_engine(database_url, **options)


engine = create_database_engine(settings.database_url)

# ---------------------------------------------------------
# 2. SESSION FACTORY: Blueprint for Transactions
# ---------------------------------------------------------
AsyncSessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

# Compatibility alias for existing imports.
AsyncSessionLocal = AsyncSessionFactory


# ---------------------------------------------------------
# 3. DECLARATIVE BASE: The Parent Class for Models
# ---------------------------------------------------------
class Base(DeclarativeBase):
    """Declarative parent for every persistent model."""


# ---------------------------------------------------------
# 4. FASTAPI DEPENDENCY: Request-scoped Sessions
# ---------------------------------------------------------
async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Yield one request-scoped session without committing implicitly.

    Service functions own transaction boundaries. Exceptions trigger a
    rollback, while the context manager always returns the connection to the
    pool.
    """

    async with AsyncSessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def dispose_database_engine() -> None:
    """Close pooled connections during application shutdown and tests."""

    await engine.dispose()
