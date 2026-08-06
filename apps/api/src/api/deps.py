"""Request-scoped dependencies.

Engine and Redis client live on `app.state`, built once in the lifespan. They are read
back through `Request` rather than imported as module globals so tests can build an
app against a throwaway container without patching anything.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One session per request. An AsyncSession is not safe to share across
    concurrent tasks, so it is never cached."""
    maker: async_sessionmaker[AsyncSession] = request.app.state.sessionmaker
    async with maker() as session:
        yield session


def get_redis(request: Request) -> Redis:
    return request.app.state.redis


SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
