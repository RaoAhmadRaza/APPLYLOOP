"""Alembic environment.

Sync, not async, on purpose: migrations are a one-shot batch job, and `asyncio.run()`
inside env.py breaks whenever something already holds an event loop. Because the app
uses psycopg3, the same `postgresql+psycopg://` URL drives both — there is no second
URL to keep in sync.
"""

import os
from logging.config import fileConfig

import db.models  # noqa: F401  — load every model so Base.metadata is complete
from alembic import context
from db.base import Base
from pgvector.sqlalchemy import HALFVEC, VECTOR
from sqlalchemy import engine_from_config, pool
from sqlalchemy.dialects import postgresql

# SQLAlchemy's Postgres reflection has no entry for pgvector's types, so autogenerate
# cannot round-trip an existing vector column without this.
postgresql.base.ischema_names["vector"] = VECTOR
postgresql.base.ischema_names["halfvec"] = HALFVEC

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def get_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env, or export it. "
            "Migrations must connect directly to Postgres, never through a pooler: "
            "advisory locks and CREATE INDEX CONCURRENTLY are session-scoped."
        )
    return url


def run_migrations_offline() -> None:
    context.configure(
        url=get_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = get_url()

    connectable = engine_from_config(configuration, prefix="sqlalchemy.", poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # NOT the Alembic default. Without it, column type changes are silently
            # missed on autogenerate.
            compare_type=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
