# APPLYLOOP

Always-on job-application engine. See [CLAUDE.md](CLAUDE.md) for the architecture,
invariants, and build order — that file is project law, this one is just how to run it.

**Current milestone: M0 (foundation).** Scope rules live in CLAUDE.md §9.

## Quick start

```bash
cp .env.example .env      # then edit POSTGRES_PASSWORD
make up                   # boots postgres+pgvector, redis, migrations, api, worker
curl localhost:8000/health
open http://localhost:8000/docs
```

`make down` stops it; `make clean` also drops the database volume.

## Layout

```
apps/api          FastAPI — the DB contract surface
apps/workers      Celery workers, one package per pipeline stage
packages/db       SQLAlchemy models + Alembic migrations (source of truth)
packages/schemas  pydantic types + the enums every CHECK constraint derives from
infra             Dockerfile
tests             unit + integration (testcontainers, real Postgres)
evals             golden set and fabrication tests (M4/M5)
```

Stages talk to each other **only** through Postgres rows. Cross-imports between
`apps/workers/*` packages are forbidden — see CLAUDE.md §3.1.

## Development

```bash
make test         # pytest (spins its own throwaway Postgres + Redis)
make lint         # ruff check + format --check
make typecheck    # mypy
make verify       # all of the above, plus a compose smoke test
```

Migrations:

```bash
make migrate                        # alembic upgrade head
make revision m="add company_id"    # autogenerate a new revision
```

Requires a running Postgres (`make up` or `docker compose up -d db`) — autogenerate
diffs the models against a live database.

## Requirements

Docker, and [uv](https://docs.astral.sh/uv/) for running anything outside a container.
Python 3.13 (uv will fetch it).
