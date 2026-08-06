.PHONY: up down logs ps migrate revision shell test lint typecheck fmt verify clean

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

# The full M0 gate, locally.
verify: lint typecheck test
	@echo "--- compose smoke ---"
	docker compose up -d --wait
	curl -fsS localhost:8000/health && echo
	curl -fsS localhost:8000/health/ready && echo
	docker compose exec -T worker celery -A workers.app inspect ping
	@echo "M0 gate: all checks passed"
