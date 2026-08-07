"""M0 gate items 2 and 3: migrations apply cleanly and pgvector is present."""

import os

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, inspect, text

from tests.conftest import ALEMBIC_INI

EXPECTED_TABLES = {
    "applications",
    "approvals",
    "companies",
    "documents",
    "events",
    "job_embeddings",
    "jobs",
    "matches",
    "evidence",
    "profiles",
    "users",
}


def test_vector_extension_is_installed(engine: Engine) -> None:
    with engine.connect() as conn:
        row = conn.execute(text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")).first()
    assert row is not None, "migration 0001 did not create the pgvector extension"


def test_all_tables_exist(engine: Engine) -> None:
    tables = set(inspect(engine).get_table_names())
    assert tables >= EXPECTED_TABLES, f"missing: {EXPECTED_TABLES - tables}"


def test_alembic_version_is_at_head(engine: Engine) -> None:
    cfg = Config(str(ALEMBIC_INI))
    head = ScriptDirectory.from_config(cfg).get_current_head()
    with engine.connect() as conn:
        current = conn.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
    assert current == head


def test_downgrade_then_upgrade_round_trips(migrated_url: str, engine: Engine) -> None:
    """A migration that cannot be undone cannot be reviewed. §6.2 says production is
    forward-only; write them reversible anyway."""
    os.environ["DATABASE_URL"] = migrated_url
    cfg = Config(str(ALEMBIC_INI))

    command.downgrade(cfg, "base")
    assert not (EXPECTED_TABLES & set(inspect(engine).get_table_names()))

    command.upgrade(cfg, "head")
    assert set(inspect(engine).get_table_names()) >= EXPECTED_TABLES


def test_no_pending_autogenerate_diff(migrated_url: str) -> None:
    """The models and the migrations agree.

    This is the test that catches "changed a model, forgot the migration" — Part 13
    rule 8's automated form.
    """
    import db.models  # noqa: F401
    from alembic.autogenerate import compare_metadata
    from alembic.migration import MigrationContext
    from db.base import Base
    from sqlalchemy import create_engine

    eng = create_engine(migrated_url)
    try:
        with eng.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            diff = compare_metadata(ctx, Base.metadata)
    finally:
        eng.dispose()

    assert diff == [], f"models drifted from migrations: {diff}"
