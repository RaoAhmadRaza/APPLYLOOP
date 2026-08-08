.PHONY: up down logs ps migrate revision shell seed ingest feeds grow dedupe test lint typecheck fmt verify verify-live verify-live-feeds verify-live-aggregator verify-live-parse verify-live-match verify-live-pool verify-live-storage verify-live-drive verify-live-tailor parse match tailor clean

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

# Pull every layer-3 feed once. Spends a real politeness budget — Remotive asks for at
# most four requests a day — so this is a deliberate command, not part of `make verify`.
feeds:
	docker compose exec -T worker python -c \
	  "from workers.scraping.feeds import FEEDS; from workers.tasks.scraping import ingest_feed; \
	   [print(s, ingest_feed.delay(s).get(timeout=120)) for s in FEEDS]"

# Reverse-index a batch of the employers layers 2 and 3 named (CLAUDE.md 4.3).
grow:
	docker compose exec -T worker python -c \
	  "from workers.tasks.scraping import grow_registry; print(grow_registry.delay().get(timeout=300))"

# Parse one profile's résumé now. make parse id=<profile-uuid>
# No-ops without LLM_API_KEY, by design — it records `profile.parse_skipped` instead.
parse:
	docker compose exec -T worker python -c \
	  "from workers.tasks.profiles import parse_profile; print(parse_profile.delay('$(id)').get(timeout=180))"

# Score one profile against the deduped pool now. make match id=<profile-uuid>
# No-ops without LLM_API_KEY *and* without MATCH_THRESHOLD, by design — it records
# `match.skipped` instead. Part 14 says that threshold comes from the golden set.
match:
	docker compose exec -T worker python -c \
	  "from workers.tasks.matching import match_profile; print(match_profile.delay('$(id)').get(timeout=600))"

# Tailor one match into a résumé and a cover letter now. make tailor id=<match-uuid>
# No-ops without LLM_API_KEY *and* without object storage, by design — a document that
# cannot be stored is not a document, so it records `tailor.skipped` instead. Only acts
# on a match still at `discovered`; a second run is a no-op by the state machine.
tailor:
	docker compose exec -T worker python -c \
	  "from workers.tasks.tailoring import tailor_match; print(tailor_match.delay('$(id)').get(timeout=600))"

# Collapse the same role seen on several sources onto one canonical row.
dedupe:
	docker compose exec -T worker python -c \
	  "from workers.tasks.scraping import dedupe_jobs; print(dedupe_jobs.delay().get(timeout=120))"

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
	docker compose exec -T worker celery -A workers.app inspect registered \
	  | grep -q workers.tasks.scraping.dedupe_jobs
	docker compose exec -T worker celery -A workers.app inspect registered \
	  | grep -q workers.tasks.profiles.parse_profile
	docker compose exec -T worker celery -A workers.app inspect registered \
	  | grep -q workers.tasks.matching.match_all
	docker compose exec -T worker celery -A workers.app inspect registered \
	  | grep -q workers.tasks.tailoring.tailor_match
	@# M5 renders inside the worker, not on the developer's machine. The Typst compiler
	@# ships in the typst wheel, so this proves the arch has one — an ImportError here is
	@# a wheel that did not build for arm64, which a passing host suite would not show.
	docker compose exec -T worker python -c "import typst, rendercv"
	@echo "M0 + M1 + M2 + M3 + M4 + M5 gates: all checks passed"

# M1 gate item 1, against the six real boards. Not in CI — a build must not go red
# because a third party had a bad afternoon.
verify-live:
	APPLYLOOP_LIVE_ATS=1 uv run pytest tests/integration/test_ats_live.py -q

# Layer 3, against the real feeds. Separate from verify-live on purpose: layer 1 is
# unmetered and can be run freely while touching an adapter, while these endpoints are
# rate-limited and Remotive asks for at most four requests a day. One variable for both
# would mean every adapter edit spends feed quota.
verify-live-feeds:
	APPLYLOOP_LIVE_FEEDS=1 uv run pytest tests/integration/test_feeds_live.py -q

# Layer 2, through the real proxy. Requires JOBSPY_PROXIES to be set — without it the
# aggregator no-ops by design and the suite skips. Spends metered residential
# bandwidth, so this is never in CI and never in a loop.
verify-live-aggregator:
	APPLYLOOP_LIVE_AGGREGATOR=1 uv run pytest tests/integration/test_aggregator_live.py -q

# M3's gate: four fixture résumés through the real model. Its own variable because this
# is the first suite that spends money rather than someone else's goodwill — one run is
# a few cents, but it should never be something a `make test` does by accident.
verify-live-parse:
	APPLYLOOP_LIVE_LLM=1 uv run pytest tests/integration/test_resume_parse_live.py -q

# M4's gate: the golden set through the real model and the real embedding endpoint,
# with nothing stubbed. Its own variable rather than sharing APPLYLOOP_LIVE_LLM because
# this one is materially more expensive than the parse suite — and because the whole
# point is that it cannot be made green by a fake. It prints the threshold the data
# supports; that number goes into MATCH_THRESHOLD.
# APPLYLOOP_MATCH_RUNS=3 because the gate is not deterministic: two consecutive passes
# over an identical set gave precision 0.62 and 0.50 at the same threshold. The chosen
# threshold must clear on all three. Override to 1 while iterating on the prompt.
verify-live-match:
	APPLYLOOP_LIVE_MATCH=1 APPLYLOOP_MATCH_RUNS=3 uv run pytest tests/integration/test_matching_live.py -q -s

# BAR.md §2's pool floor and per-filter cap, which are properties of the filters against
# the REAL pool and cannot be measured in the golden harness — §7 pins that to stored
# payloads, so its pool is 16 rows per profile. Read-only: no model, no writes, no cost.
verify-live-pool:
	APPLYLOOP_LIVE_POOL=1 uv run pytest tests/integration/test_filters_live.py -q -s

# Object storage against a real bucket. Needs STORAGE_* set; skips otherwise, because
# unconfigured storage is a supported state.
verify-live-storage:
	APPLYLOOP_LIVE_STORAGE=1 uv run pytest tests/integration/test_storage_live.py -q

# M5's mirror clause, against a real Shared Drive. Writes two small files into it.
# Unlike the targets above, this one FAILS rather than skips when the variable is set and
# the credentials are not — an opt-in run that finds no key is a mistake, not a state.
verify-live-drive:
	APPLYLOOP_LIVE_DRIVE=1 uv run pytest tests/integration/test_drive_live.py -q -s

# M5's gate: the fabrication cases through the real strong model, with nothing stubbed.
# Its own variable because this is the most expensive suite in the repo — a strong model,
# two calls per case — and because the whole point is that a fake cannot make it green.
# Roughly $2-4 a run. Read evals/fabrication/BAR.md §2 before quoting any number it prints.
verify-live-tailor:
	APPLYLOOP_LIVE_TAILOR=1 uv run pytest tests/integration/test_tailoring_live.py -q -s
