"""Async SQLAlchemy engine, session factory and FastAPI dependency."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings, get_settings

_engine: AsyncEngine | None = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def create_engine(settings: Settings | None = None) -> AsyncEngine:
    """Build a pooled async engine from settings."""
    settings = settings or get_settings()
    return create_async_engine(
        settings.sqlalchemy_database_uri,
        echo=settings.db_echo,
        pool_size=settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_timeout=settings.db_pool_timeout,
        pool_recycle=settings.db_pool_recycle,
        pool_pre_ping=True,
        # Bounds the handshake, not just the pool wait — see the setting's
        # comment. psycopg's own default is 130 seconds, which is what made an
        # unreachable database look like a hung login.
        connect_args={"connect_timeout": settings.db_connect_timeout_seconds},
        future=True,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Build a session factory bound to ``engine``."""
    return async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        autoflush=False,
        autocommit=False,
        expire_on_commit=False,
    )


def get_engine() -> AsyncEngine:
    """Return the lazily created process-wide engine."""
    global _engine
    if _engine is None:
        _engine = create_engine()
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    """Return the lazily created process-wide session factory."""
    global _session_factory
    if _session_factory is None:
        _session_factory = create_session_factory(get_engine())
    return _session_factory


def configure(engine: AsyncEngine) -> None:
    """Install a specific engine/session factory (used by the test suite)."""
    global _engine, _session_factory
    _engine = engine
    _session_factory = create_session_factory(engine)


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session.

    Rolls back on unhandled errors and always closes the session.
    """
    async with get_session_factory()() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def check_database_connection() -> bool:
    """Return ``True`` when a trivial query succeeds within a short budget.

    ``pool_timeout`` only bounds waiting for a free connection, not the
    handshake behind it, so the probe gets its own wall-clock bound: a filtered
    port or a wedged server would otherwise block until the OS TCP timeout and
    hang callers that must answer. Overrunning is reported as unavailable.
    """
    settings = get_settings()
    connection = get_engine().connect()
    try:
        async with asyncio.timeout(settings.db_probe_timeout_seconds):
            async with connection:
                await connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False
    finally:
        # Covers a connect aborted inside the timeout: __aenter__ never
        # completed, so __aexit__ never ran to hand the connection back.
        # Closing an unstarted AsyncConnection raises AsyncContextNotStarted,
        # and an exception raised here escapes past the handler above — so an
        # unreachable database answered /health with a 500 instead of the
        # degraded status it is documented to return.
        with suppress(Exception):
            await connection.close()
