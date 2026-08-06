"""Celery application.

Tasks live under `workers.tasks`, one module per pipeline stage as they arrive
(M1 scraping, M4 matching, M5 tailoring, ...). Cross-stage imports between those
modules are forbidden by §3.1 — stages talk through Postgres rows, never function
calls.

The API never imports this module. It enqueues by task name with
`send_task("workers.tasks.health.ping")`, which keeps Celery out of the API image.
"""

from celery import Celery
from db.session import make_sync_engine, make_sync_sessionmaker

from workers.settings import settings

app = Celery("applyloop", broker=str(settings.redis_url), backend=str(settings.redis_url))

app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    # At-least-once: a worker killed mid-task re-delivers rather than losing the job.
    # The cost is duplicate execution, which §3.4's unique constraints absorb.
    task_acks_late=True,
    worker_prefetch_multiplier=1,
)

app.autodiscover_tasks(["workers.tasks"])

# Engine per worker process. NullPool (see db.session) means forked children never
# share inherited sockets, so no worker_process_init reset hook is needed.
engine = make_sync_engine(str(settings.database_url))
SessionLocal = make_sync_sessionmaker(engine)
