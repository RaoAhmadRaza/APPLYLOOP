"""The Celery wiring for M1: task names and the beat entry.

Deliberately thin, and deliberately network-free. The logic lives in `workers.scraping`
and is covered without a broker by test_ingest.py / test_registry.py; the broker round
trip itself is covered by test_celery_noop.py. What is left to prove here is only that
the names the schedule and the dispatcher reference actually resolve.

Nothing here starts a worker. Letting one run would have `ingest_all` fan out to every
seeded board and hit thirty live ATS endpoints from CI. The genuinely unattended run —
beat fires, jobs appear, nobody typed anything — is proven in the compose smoke, which
is the only place it can be proven honestly.
"""

import importlib
import inspect
from typing import Any
from unittest.mock import patch

import pytest


@pytest.fixture
def celery_app(redis_url: str, migrated_url: str, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("DATABASE_URL", migrated_url)

    import workers.app
    import workers.settings
    import workers.tasks.scraping

    # get_settings is lru_cached; without this the reload reuses the stale Settings.
    workers.settings.get_settings.cache_clear()
    importlib.reload(workers.app)
    importlib.reload(workers.tasks.scraping)
    return workers.app.app


def test_the_beat_entry_points_at_a_task_that_exists(celery_app: Any) -> None:
    """M1 gate item 4 hangs off this one string. A rename that misses the schedule
    leaves the pipeline silently never running — no error, just no rows."""
    schedule = celery_app.conf.beat_schedule["ingest-ats-boards"]

    assert schedule["task"] == "workers.tasks.scraping.ingest_all"
    assert schedule["task"] in celery_app.tasks


def test_dedupe_has_its_own_beat_entry(celery_app: Any) -> None:
    """Its own entry rather than a chord after ingest: the pass converges to a fixed
    point from any starting state, so one failing board must not block it."""
    entry = celery_app.conf.beat_schedule["dedupe-jobs"]

    assert entry["task"] == "workers.tasks.scraping.dedupe_jobs"
    assert entry["task"] in celery_app.tasks


def test_the_aggregator_no_ops_without_a_proxy(celery_app: Any) -> None:
    """A safety interlock, not a guard clause. Running LinkedIn or Indeed from a bare
    datacentre or a developer's home IP burns that IP (§7.4). There is deliberately no
    override flag, so this is also the assertion that none was added."""
    import workers.tasks.scraping as tasks

    with patch.object(tasks.aggregate_search, "delay") as delay:
        dispatched = tasks.aggregate_all()

    assert dispatched == 0
    assert delay.call_args_list == []


def test_every_feed_has_a_beat_entry(celery_app: Any) -> None:
    """A feed in the registry with no schedule silently never runs — no error, no rows,
    exactly the failure §3.7 warns about. The entries are built from FEEDS so this
    cannot drift, and this asserts that they were."""
    from workers.scraping.feeds import FEEDS

    for source, module in FEEDS.items():
        entry = celery_app.conf.beat_schedule[f"feed-{source}"]
        assert entry["task"] == "workers.tasks.scraping.ingest_feed"
        assert entry["task"] in celery_app.tasks
        assert entry["args"] == (source,)
        assert entry["schedule"].total_seconds() == module.INTERVAL_HOURS * 3600


@pytest.mark.parametrize(
    "name",
    [
        "workers.tasks.scraping.ingest_all",
        "workers.tasks.scraping.ingest_company",
        "workers.tasks.scraping.ingest_feed",
        "workers.tasks.scraping.dedupe_jobs",
        "workers.tasks.scraping.grow_registry",
        "workers.tasks.scraping.aggregate_all",
        "workers.tasks.scraping.aggregate_search",
        "workers.tasks.scraping.detect_company",
        "workers.tasks.scraping.seed_registry",
    ],
)
def test_every_task_is_registered_under_its_dotted_path(celery_app: Any, name: str) -> None:
    """Beat and `make ingest` both reach these by string, never by import."""
    assert name in celery_app.tasks


def test_no_task_takes_a_proxy_argument(celery_app: Any) -> None:
    """M2 gate, the half that lives in the task layer. Celery logs task arguments at
    INFO and writes them to the Redis result backend, so a proxy parameter would print
    `user:pass@host` into both (Part 13 rule 9). The aggregator reads its credential
    from settings inside the task instead.

    Iterates the live registry rather than a hand-written list, so a task added later
    is covered without anyone remembering to come back here.
    """
    ours = {name: task for name, task in celery_app.tasks.items() if name.startswith("workers.")}
    assert ours, "no tasks registered — TASK_MODULES is broken"

    for name, task in ours.items():
        params = inspect.signature(task.run).parameters
        assert not any("prox" in p.lower() for p in params), f"{name} takes a proxy argument"


def test_ingest_all_enqueues_one_task_per_active_board(celery_app: Any) -> None:
    """The dispatcher holds no business logic — it selects and fans out, nothing more."""
    import workers.tasks.scraping as tasks
    from db.models import Company
    from schemas.enums import AtsType, CompanyStatus

    active = Company(name="A", ats_type=AtsType.LEVER.value, ats_slug="a")
    retired = Company(
        name="B",
        ats_type=AtsType.LEVER.value,
        ats_slug="b",
        status=CompanyStatus.RETIRED.value,
    )
    # §4.3's negative cache. There is no adapter behind `other`, so fanning out to it
    # would be a KeyError inside the worker.
    unresolvable = Company(name="C", ats_type=AtsType.OTHER.value, ats_slug="c")

    with tasks.SessionLocal() as setup:
        setup.add_all([active, retired, unresolvable])
        setup.commit()
        active_id, retired_id = str(active.id), str(retired.id)
        unresolvable_id = str(unresolvable.id)

    try:
        with patch.object(tasks.ingest_company, "delay") as delay:
            dispatched = tasks.ingest_all()

        enqueued = {call.args[0] for call in delay.call_args_list}
        assert active_id in enqueued
        # Retired slugs are skipped — that is what auto-retiring them is for.
        assert retired_id not in enqueued
        assert unresolvable_id not in enqueued
        assert dispatched == len(enqueued)
    finally:
        with tasks.SessionLocal() as cleanup:
            cleanup.query(Company).delete()
            cleanup.commit()
