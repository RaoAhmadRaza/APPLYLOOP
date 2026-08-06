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


@pytest.mark.parametrize(
    "name",
    [
        "workers.tasks.scraping.ingest_all",
        "workers.tasks.scraping.ingest_company",
        "workers.tasks.scraping.detect_company",
        "workers.tasks.scraping.seed_registry",
    ],
)
def test_every_task_is_registered_under_its_dotted_path(celery_app: Any, name: str) -> None:
    """Beat and `make ingest` both reach these by string, never by import."""
    assert name in celery_app.tasks


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

    with tasks.SessionLocal() as setup:
        setup.add_all([active, retired])
        setup.commit()
        active_id, retired_id = str(active.id), str(retired.id)

    try:
        with patch.object(tasks.ingest_company, "delay") as delay:
            dispatched = tasks.ingest_all()

        enqueued = {call.args[0] for call in delay.call_args_list}
        assert active_id in enqueued
        # Retired slugs are skipped — that is what auto-retiring them is for.
        assert retired_id not in enqueued
        assert dispatched == len(enqueued)
    finally:
        with tasks.SessionLocal() as cleanup:
            cleanup.query(Company).delete()
            cleanup.commit()
