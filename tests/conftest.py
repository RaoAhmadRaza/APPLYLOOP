"""Test fixtures.

Postgres and Redis come from throwaway containers, using the same images compose
runs, so local and CI take one code path.

The schema is created by running Alembic, never by `metadata.create_all()`. That way
the migrations themselves are under test on every run — which is M0 gate item 2, and
catches the class of bug where a model change lands without a migration.
"""

import os
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from db.session import make_sync_engine, make_sync_sessionmaker
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer
from testcontainers.core.container import DockerContainer
from testcontainers.core.wait_strategies import LogMessageWaitStrategy

REPO_ROOT = Path(__file__).resolve().parents[1]
ALEMBIC_INI = REPO_ROOT / "packages" / "db" / "alembic.ini"

# Same tags as compose.yml. Drifting these apart is how "works locally, fails in CI"
# starts.
POSTGRES_IMAGE = "pgvector/pgvector:0.8.6-pg18-trixie"
REDIS_IMAGE = "redis:8-alpine"


@pytest.fixture(scope="session")
def postgres_url() -> Iterator[str]:
    with PostgresContainer(POSTGRES_IMAGE, driver="psycopg") as container:
        # driver="psycopg" is required: the default is psycopg2, which is not
        # installed, and the resulting URL would fail to connect.
        yield container.get_connection_url()


@pytest.fixture(scope="session")
def redis_url() -> Iterator[str]:
    # Generic DockerContainer rather than testcontainers[redis]: that extra requires
    # redis>=7 while Celery's kombu caps the client below 6.5.
    container = DockerContainer(REDIS_IMAGE).with_exposed_ports(6379)
    container.waiting_for(LogMessageWaitStrategy("Ready to accept connections"))
    container.start()
    try:
        host = container.get_container_host_ip()
        port = container.get_exposed_port(6379)
        url = f"redis://{host}:{port}/0"
        # Exported for the same reason migrated_url exports DATABASE_URL: importing
        # api.main runs `app = create_app()` at module scope, and that entrypoint is
        # supposed to demand a complete environment. Without this the suite passes
        # only on a machine that happens to have a .env.
        os.environ["REDIS_URL"] = url
        yield url
    finally:
        container.stop()


@pytest.fixture(scope="session")
def migrated_url(postgres_url: str) -> Iterator[str]:
    """Runs `alembic upgrade head` once for the session."""
    os.environ["DATABASE_URL"] = postgres_url
    cfg = Config(str(ALEMBIC_INI))
    command.upgrade(cfg, "head")
    yield postgres_url


@pytest.fixture(scope="session")
def engine(migrated_url: str) -> Iterator[Engine]:
    eng = make_sync_engine(migrated_url)
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    """Per-test isolation by outer transaction + rollback.

    The session joins a connection-level transaction that is always rolled back, so
    tests never see each other's rows and the schema is created only once.
    """
    connection = engine.connect()
    transaction = connection.begin()
    maker = make_sync_sessionmaker(connection)  # type: ignore[arg-type]
    db_session = maker()
    try:
        yield db_session
    finally:
        # Roll the session back first. A test that asserted on IntegrityError leaves
        # the transaction already deassociated, and rolling back the outer
        # transaction directly would warn.
        db_session.rollback()
        db_session.close()
        if transaction.is_active:
            transaction.rollback()
        connection.close()


def _truncate_all(engine: Engine) -> None:
    """Wipe every table between API tests.

    The `session` fixture's rollback cannot isolate these: requests go through the
    real app, which owns its own engine and genuinely commits. Without this, the
    second test to create a user hits the unique email constraint and 409s.
    """
    import db.models  # noqa: F401
    from db.base import Base

    names = ", ".join(t.name for t in Base.metadata.sorted_tables)
    with engine.begin() as conn:
        conn.execute(text(f"TRUNCATE {names} RESTART IDENTITY CASCADE"))


@pytest.fixture
async def client(migrated_url: str, redis_url: str, engine: Engine) -> AsyncIterator["object"]:
    """httpx AsyncClient bound to a real app instance.

    ASGITransport does not run lifespan, so LifespanManager is required or
    app.state.sessionmaker would never exist.
    """
    from api.main import create_app
    from api.settings import Settings
    from asgi_lifespan import LifespanManager
    from httpx import ASGITransport, AsyncClient

    cfg = Settings(database_url=migrated_url, redis_url=redis_url)  # type: ignore[arg-type]
    app = create_app(cfg)

    try:
        async with LifespanManager(app):
            transport = ASGITransport(app=app)
            # AsyncClient(app=...) was removed in httpx 0.28 — tutorials predate it.
            async with AsyncClient(transport=transport, base_url="http://test") as ac:
                yield ac
    finally:
        _truncate_all(engine)


@pytest.fixture
def db_has_vector_extension(session: Session) -> bool:
    row = session.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first()
    return row is not None
