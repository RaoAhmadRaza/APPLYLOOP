"""Celery application.

Tasks live under `workers.tasks`, one module per pipeline stage as they arrive
(M1 scraping, M4 matching, M5 tailoring, ...). Cross-stage imports between those
modules are forbidden by §3.1 — stages talk through Postgres rows, never function
calls.

The API never imports this module. It enqueues by task name with
`send_task("workers.tasks.health.ping")`, which keeps Celery out of the API image.
"""

from datetime import timedelta

from celery import Celery
from db.session import make_sync_engine, make_sync_sessionmaker

from workers.settings import get_settings

# Module scope: this IS the worker entrypoint, so a bad environment must fail here.
settings = get_settings()

# Every module holding tasks, listed explicitly.
#
# NOT `autodiscover_tasks(["workers.tasks"])`, which was here and silently did nothing:
# autodiscover appends `related_name` to each entry, so it looked for a module called
# `workers.tasks.tasks`. Nothing matched, nothing registered, and the worker answered
# `celery inspect ping` perfectly while rejecting every real task with
# "Received unregistered task". The test suite hid it by importing the task modules
# directly, which registers them as a side effect.
#
# One line per stage as they arrive, and `inspect registered` in the compose smoke is
# what proves the list is complete — a unit test cannot, because importing a task module
# to check it is exactly what masks the bug.
TASK_MODULES = [
    "workers.tasks.health",
    "workers.tasks.scraping",
]

app = Celery(
    "applyloop",
    broker=str(settings.redis_url),
    backend=str(settings.redis_url),
    include=TASK_MODULES,
)

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    # At-least-once: a worker killed mid-task re-delivers rather than losing the job.
    # The cost is duplicate execution, which §3.4's unique constraints absorb.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    # M1 gate item 4: a scheduled run populates jobs unattended.
    #
    # Beat, not n8n. §7.1 names n8n as the orchestrator, but that is M7's milestone and
    # standing it up here would pull M7 forward. This is scheduling with no business
    # logic in it — the entry names a task and an interval, nothing else — so M7 can
    # replace it with cron -> webhook without touching a line of the ingest code.
    #
    beat_schedule={
        "ingest-ats-boards": {
            "task": "workers.tasks.scraping.ingest_all",
            "schedule": timedelta(minutes=settings.ingest_interval_minutes),
        },
    },
)

# Engine per worker process. NullPool (see db.session) means forked children never
# share inherited sockets, so no worker_process_init reset hook is needed.
engine = make_sync_engine(str(settings.database_url))
SessionLocal = make_sync_sessionmaker(engine)
