"""Engine and session factories.

Pure factories with no module-level engines and no import-time settings read. The API
composes them in its lifespan, Celery composes them in its app module, and tests
compose them from a throwaway container URL. That removes import-order coupling and
makes every consumer explicit about which engine it wants.

One URL serves both: `postgresql+psycopg://` is sync or async depending on which
factory you call. That is the whole reason psycopg3 was chosen over asyncpg — no
second URL to drift out of sync with the first.
"""

from collections.abc import AsyncIterator, Iterator

from sqlalchemy import Engine, create_engine
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool


def make_async_engine(url: str) -> AsyncEngine:
    """For the API. Pooled, with pre-ping so a recycled Postgres connection surfaces
    as a reconnect rather than a 500."""
    return create_async_engine(url, pool_size=5, max_overflow=5, pool_pre_ping=True)


def make_sync_engine(url: str) -> Engine:
    """For Celery tasks and Alembic.

    `NullPool` on purpose: Celery's prefork children inherit the parent's pooled
    sockets and corrupt each other's protocol state. A connection per task costs
    nothing at this scale and sidesteps the problem entirely rather than patching it
    with a worker_process_init hook.
    """
    return create_engine(url, poolclass=NullPool)


def make_async_sessionmaker(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """`expire_on_commit=False` is mandatory, not a preference: the default expires
    attributes on commit, and re-loading them is implicit IO, which raises under
    asyncio."""
    return async_sessionmaker(engine, expire_on_commit=False)


def make_sync_sessionmaker(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(engine, expire_on_commit=False)


async def session_scope(
    maker: async_sessionmaker[AsyncSession],
) -> AsyncIterator[AsyncSession]:
    """One session per request or task. An AsyncSession is not safe to share across
    concurrent tasks."""
    async with maker() as session:
        yield session


def sync_session_scope(maker: sessionmaker[Session]) -> Iterator[Session]:
    with maker() as session:
        yield session
