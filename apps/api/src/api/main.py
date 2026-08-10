"""Core API — the DB contract surface (CLAUDE.md §5.1)."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from db.session import make_async_engine, make_async_sessionmaker
from fastapi import FastAPI
from redis.asyncio import Redis

from api.routers import build_crud_routers, documents, health, matches, pipeline, resume
from api.settings import Settings, get_settings


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    cfg: Settings = app.state.settings
    engine = make_async_engine(str(cfg.database_url))
    app.state.engine = engine
    app.state.sessionmaker = make_async_sessionmaker(engine)
    app.state.redis = Redis.from_url(str(cfg.redis_url), decode_responses=True)

    yield

    await app.state.redis.aclose()
    # Without this you get "Event loop is closed" noise on every reload.
    await engine.dispose()


def create_app(cfg: Settings | None = None) -> FastAPI:
    # Resolved here, not at import: a bad environment still kills the process at boot
    # because `app = create_app()` runs at module scope below.
    cfg = cfg or get_settings()
    app = FastAPI(
        title=cfg.project_name,
        version="0.1.0",
        lifespan=lifespan,
    )
    app.state.settings = cfg

    app.include_router(health.router)
    # Before the CRUD routers, all four: each mounts a literal sub-path under a prefix the
    # generic routers also claim, and /profiles/{row_id} would otherwise shadow
    # /profiles/{id}/resume. Any future custom route belongs above this line too.
    app.include_router(resume.router)
    app.include_router(pipeline.router)
    app.include_router(matches.router)
    app.include_router(documents.router)
    for router in build_crud_routers():
        app.include_router(router)

    return app


app = create_app()
