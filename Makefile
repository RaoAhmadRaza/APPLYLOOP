.PHONY: up down logs ps migrate revision shell seed ingest test lint typecheck fmt verify verify-live clean

# `make up` is the one command that boots the stack (M0 gate item 1).
up:
	docker compose up -d --wait

down:
	docker compose down

# Also drops the volume. Use when a migration needs a clean slate.
clean:
	docker compose down -v

logs:
	docker compose logs -f

ps:
	docker compose ps

migrate:
	docker compose run --rm migrate

# make revision m="add company_id to jobs"
revision:
	uv run alembic -c packages/db/alembic.ini revision --autogenerate -m "$(m)"

shell:
	docker compose exec db psql -U $${POSTGRES_USER:-applyloop} -d $${POSTGRES_DB:-applyloop}

# Populate the slug registry with known boards (§4.3 seed). Idempotent.
seed:
	docker compose exec -T worker python -c \
	  "from workers.tasks.scraping import seed_registry; print(seed_registry())"

# Run one ingest pass now instead of waiting for the next beat tick.
ingest:
	docker compose exec -T worker python -c \
	  "from workers.tasks.scraping import ingest_all; print(ingest_all.delay().get(timeout=60))"

test:
	uv run pytest

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

# The full M0 + M1 gate, locally.
verify: lint typecheck test
	@echo "--- compose smoke ---"
	docker compose up -d --wait
	curl -fsS localhost:8000/health && echo
	curl -fsS localhost:8000/health/ready && echo
	docker compose exec -T worker celery -A workers.app inspect ping
	@# `inspect ping` passes with zero tasks registered — that is how a broken
	@# autodiscover survived M0. Ask what the worker will actually accept.
	docker compose exec -T worker celery -A workers.app inspect registered \
	  | grep -q workers.tasks.scraping.ingest_all
	@echo "M0 + M1 gates: all checks passed"

# M1 gate item 1, against the six real boards. Not in CI — a build must not go red
# because a third party had a bad afternoon.
verify-live:
	APPLYLOOP_LIVE_ATS=1 uv run pytest tests/integration/test_ats_live.py -q
