"""Enqueue a worker task by name.

The API depends on `celery` the library but never on the `workers` package — that is
the direction rule in the workspace root, and it is what keeps pandas, JobSpy and
markitdown out of anything the API imports. A bare `Celery` instance knows the broker
and nothing else; `send_task` takes a dotted string, so the task's code need not exist
in this process at all.

Module-level and lazily connected: `Celery(...)` opens no socket, and the first
`send_task` establishes the connection. Nothing here needs teardown.
"""

from typing import Any

from celery import Celery

from api.settings import get_settings

# Task names, spelled once. A typo in a dotted string fails at runtime on the broker
# rather than at import, so the constant is the only place it can be wrong — and
# `tests/integration/test_profile_upload.py` asserts it matches the registered task.
PARSE_PROFILE = "workers.tasks.profiles.parse_profile"
TAILOR_MATCH = "workers.tasks.tailoring.tailor_match"

_client: Celery | None = None


def enqueue(name: str, *args: Any) -> None:
    """Fire and forget. The result lands in the database, not in a return value (§3.1)."""
    global _client  # noqa: PLW0603 — one broker connection per process, built on demand
    if _client is None:
        _client = Celery(broker=str(get_settings().redis_url))
    _client.send_task(name, args=list(args))
