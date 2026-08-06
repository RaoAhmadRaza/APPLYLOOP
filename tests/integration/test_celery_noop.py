"""M0 gate item 6: a no-op Celery task enqueues and completes.

Deliberately NOT `task_always_eager`. Eager mode runs the task inline and never
touches the broker, so it would pass on a completely broken Redis — proving nothing
about "enqueues". This uses a real Redis container and a real worker.

`start_worker` runs the worker in-process on a thread, which also sidesteps macOS
fork() aborts under the prefork pool.
"""

from collections.abc import Iterator

import pytest
from celery.contrib.testing.worker import start_worker


@pytest.fixture
def celery_app(redis_url: str, migrated_url: str, monkeypatch: pytest.MonkeyPatch) -> object:
    monkeypatch.setenv("REDIS_URL", redis_url)
    monkeypatch.setenv("DATABASE_URL", migrated_url)

    # Imported inside the fixture so the patched env is in place when the module-level
    # get_settings() runs.
    import importlib

    import workers.app
    import workers.settings
    import workers.tasks.health

    # get_settings is lru_cached; without this the reload reuses the stale Settings.
    workers.settings.get_settings.cache_clear()
    importlib.reload(workers.app)
    importlib.reload(workers.tasks.health)
    return workers.app.app


@pytest.fixture
def worker(celery_app: object) -> Iterator[None]:
    with start_worker(celery_app, perform_ping_check=False, shutdown_timeout=30):
        yield


def test_ping_enqueues_and_completes(celery_app: object, worker: None) -> None:
    import workers.tasks.health

    result = workers.tasks.health.ping.delay()
    assert result.get(timeout=30) == "pong"
    assert result.successful()


def test_task_is_reachable_by_name(celery_app: object, worker: None) -> None:
    """The API enqueues by name rather than importing the worker package (§3.1). If
    the registered name drifts, that call silently never runs — so pin it here."""
    result = celery_app.send_task("workers.tasks.health.ping")  # type: ignore[attr-defined]
    assert result.get(timeout=30) == "pong"


def test_broker_is_the_redis_container(celery_app: object, redis_url: str) -> None:
    assert celery_app.conf.broker_url == redis_url  # type: ignore[attr-defined]
