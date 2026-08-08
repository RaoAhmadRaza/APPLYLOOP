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

from workers.scraping import feeds
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
    # M3. No beat entry: parsing fires on upload, not on a clock.
    "workers.tasks.profiles",
    "workers.tasks.matching",
    # M5. No beat entry either, and for a different reason: every tailored match is a
    # strong-model call, so an unattended tick is real money. M7 owns the schedule.
    "workers.tasks.tailoring",
]

# Imported for the beat schedule below, not for the tasks — one entry per feed, keyed by
# the registry so adding a module cannot leave it unscheduled.
FEEDS = feeds.FEEDS

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
    # Each layer-3 feed gets its own entry on its own interval, built from the registry
    # so a feed cannot be added and then silently never run. The interval lives on the
    # feed module rather than in settings because it is a property of that provider's
    # terms — Remotive's own response asks for at most four requests a day — and it does
    # not change between staging and production.
    beat_schedule={
        "ingest-ats-boards": {
            "task": "workers.tasks.scraping.ingest_all",
            "schedule": timedelta(minutes=settings.ingest_interval_minutes),
        },
        **{
            f"feed-{source}": {
                "task": "workers.tasks.scraping.ingest_feed",
                "args": (source,),
                "schedule": timedelta(hours=module.INTERVAL_HOURS),
            }
            for source, module in FEEDS.items()
        },
        # Its own entry rather than a chord after ingest: the pass converges to a fixed
        # point from any starting state, so *when* it runs is not load-bearing, and a
        # chord would let one failing board block the dedupe of every other source.
        "dedupe-jobs": {
            "task": "workers.tasks.scraping.dedupe_jobs",
            "schedule": timedelta(minutes=settings.ingest_interval_minutes),
        },
        # §4.3's reverse-index. Hourly rather than per-ingest: it works through a capped
        # batch each run, so the useful knob is how often it gets a turn.
        "grow-registry": {
            "task": "workers.tasks.scraping.grow_registry",
            "schedule": timedelta(minutes=settings.grow_interval_minutes),
        },
        # Layer 2. Twice a day rather than four times, because every request here spends
        # residential bandwidth and carries block risk. No-ops without a proxy.
        "aggregate-jobspy": {
            "task": "workers.tasks.scraping.aggregate_all",
            "schedule": timedelta(minutes=settings.aggregate_interval_minutes),
        },
        # M4. Its own entry for the same reason dedupe has one: scoring converges — a job
        # already scored is excluded by a WHERE clause — so *when* it runs is not
        # load-bearing, and chaining it behind ingest would let one failing board stall
        # every user's matches. No-ops without an API key, and without a threshold.
        "match-profiles": {
            "task": "workers.tasks.matching.match_all",
            "schedule": timedelta(minutes=settings.match_interval_minutes),
        },
    },
)

# Engine per worker process. NullPool (see db.session) means forked children never
# share inherited sockets, so no worker_process_init reset hook is needed.
engine = make_sync_engine(str(settings.database_url))
SessionLocal = make_sync_sessionmaker(engine)
