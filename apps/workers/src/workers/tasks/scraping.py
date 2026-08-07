"""Celery entrypoints for M1 ingestion.

The split is deliberate: everything here opens a session, opens a client, calls into
`workers.scraping`, and commits. All the logic under test lives in that package as plain
functions, so the integration suite can seed the tables, run the stage and assert the
delta without a broker in the way (Part 10).

Task names are pinned to the dotted module path, matching `tasks/health.py`, because the
API and the beat schedule both reach these by string rather than by import.
"""

from dataclasses import asdict
from typing import Any
from uuid import UUID

import httpx
from db.models import Company
from schemas.enums import AtsType, CompanyStatus
from sqlalchemy import select

from workers.app import SessionLocal, app
from workers.scraping import aggregator, dedupe, feed, grow, http, ingest, registry
from workers.scraping import detect as detection
from workers.scraping.feeds import FEEDS
from workers.settings import get_settings


@app.task(name="workers.tasks.scraping.ingest_all")
def ingest_all() -> int:
    """Fan out one `ingest_company` per active board. Holds no business logic itself."""
    with SessionLocal() as session:
        company_ids = list(
            session.scalars(
                select(Company.id).where(
                    Company.status == CompanyStatus.ACTIVE.value,
                    # §4.3's negative cache writes `other` rows for employers whose ATS
                    # we could not find. They are written as `error` and so already
                    # excluded, but there is no adapter behind `other` and reaching
                    # ADAPTERS[AtsType.OTHER] is a KeyError in production — one clause
                    # makes that impossible rather than merely unlikely.
                    Company.ats_type != AtsType.OTHER.value,
                )
            )
        )

    for company_id in company_ids:
        ingest_company.delay(str(company_id))
    return len(company_ids)


@app.task(
    bind=True,
    name="workers.tasks.scraping.ingest_company",
    # Retries come from Celery rather than a retry library: exponential backoff with
    # jitter, already configured, one less dependency. Part 13 rule 12 rules out the
    # other half of the usual answer — the ATS layer is never proxied.
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,
    retry_jitter=True,
    max_retries=3,
)
def ingest_company(self: Any, company_id: str) -> dict[str, int] | None:
    with SessionLocal() as session, http.client() as client:
        company = session.get(Company, UUID(company_id))
        if company is None:
            # Retired and deleted between fan-out and execution. Not an error.
            return None

        try:
            result = ingest.ingest_company(session, client, company)
        except httpx.HTTPError as error:
            # Only count the failure once the retries are spent. Counting each attempt
            # would burn through RETIRE_AFTER_FAILURES on a single bad afternoon and
            # retire a board that was merely rate-limited.
            if self.request.retries >= self.max_retries:
                ingest.record_failure(session, company, str(error))
                session.commit()
            raise

        ingest.record_success(session, company, result)
        session.commit()
        return asdict(result)


@app.task(
    name="workers.tasks.scraping.ingest_feed",
    autoretry_for=(httpx.HTTPError,),
    retry_backoff=True,
    retry_jitter=True,
    # Two rather than three: every retry re-runs the whole pass, and on these hosts that
    # spends a politeness budget rather than just time.
    max_retries=2,
)
def ingest_feed(source: str) -> dict[str, int]:
    """Pull one layer-3 feed. Beat fires one of these per feed, on its own interval."""
    settings = get_settings()
    feed_module = FEEDS[source]
    with SessionLocal() as session, http.client() as client:
        result = feed.ingest_feed(
            session,
            client,
            feed_module,
            delay=settings.feed_page_delay_seconds,
            volume_floor=settings.feed_volume_floor,
        )
        if not feed_module.COMPLETE:
            # A paginated feed can never prove a posting is gone, so age does the work.
            feed.close_stale(session, source, settings.feed_stale_days)
        session.commit()
        return asdict(result)


@app.task(name="workers.tasks.scraping.dedupe_jobs")
def dedupe_jobs() -> dict[str, int]:
    """Collapse the same role seen on several sources onto one canonical row (§4.2)."""
    with SessionLocal() as session:
        result = dedupe.dedupe_jobs(session)
        session.commit()
        return asdict(result)


@app.task(name="workers.tasks.scraping.aggregate_all")
def aggregate_all() -> int:
    """Fan out one search per (site, term, location). Holds no business logic itself.

    No-ops when JOBSPY_PROXIES is empty. That is a safety interlock, not a guard clause:
    §7.4 scopes proxies to this layer precisely because it is the layer that gets
    blocked, and running LinkedIn or Indeed from a bare datacentre or a developer's home
    IP burns that IP and inflates §8.2's block rate for everyone. There is deliberately
    no override flag — a developer who wants to try it unproxied can do so in a shell;
    the *scheduled* path stays interlocked.
    """
    if not get_settings().jobspy_proxies:
        with SessionLocal() as session:
            # §3.7: a layer that quietly does nothing is the failure mode, so say so.
            ingest.record(session, "aggregate.skipped", {"reason": "no proxy configured"})
            session.commit()
        return 0

    dispatched = 0
    for site in aggregator.SITES:
        for term, location in aggregator.SEARCHES:
            aggregate_search.delay(site, term, location)
            dispatched += 1
    return dispatched


@app.task(
    name="workers.tasks.scraping.aggregate_search",
    # Per worker node. ponytail: a Redis token bucket if a second node ever appears.
    rate_limit="6/m",
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    # Two rather than three: a retry re-runs the whole search against a host that has
    # already shown signs of not wanting us.
    max_retries=2,
)
def aggregate_search(site: str, term: str, location: str) -> dict[str, int]:
    """One aggregator search, upserted through M1's diff engine."""
    settings = get_settings()
    # Read from settings inside the task, never passed as an argument: Celery logs task
    # arguments at INFO and writes them to the Redis result backend, so a proxy
    # parameter would print `user:pass@host` into both (Part 13 rule 9).
    proxies = [proxy.get_secret_value() for proxy in settings.jobspy_proxies]

    jobs = aggregator.scrape(site, term, location, proxies)
    with SessionLocal() as session:
        # No company_id: §4.3's reverse-index resolves the employer later, from the
        # job_url_direct this layer stores.
        _, inserted, updated = ingest.upsert(session, jobs, None)
        # `close_missing` is deliberately NOT called. An aggregator search is a query,
        # not a board — "this job did not come back in today's python developer search"
        # means it fell off page two, not that it closed. Closing on absence here would
        # mass-close live jobs on the first throttled run.
        ingest.record(
            session,
            "aggregate.run",
            {
                "site": site,
                "term": term,
                "location": location,
                "fetched": len(jobs),
                "inserted": inserted,
                "updated": updated,
            },
        )
        session.commit()
    return {"fetched": len(jobs), "inserted": inserted, "updated": updated}


@app.task(name="workers.tasks.scraping.grow_registry")
def grow_registry() -> dict[str, int]:
    """§4.3's reverse-index: resolve employers layers 2 and 3 named, and link their rows.

    One detection unlocks that employer's whole board on the next `ingest_all`, which is
    what makes coverage compound rather than plateau.
    """
    settings = get_settings()
    with SessionLocal() as session, http.client() as client:
        result = grow.grow(
            session,
            client,
            batch=settings.grow_batch,
            min_jobs=settings.grow_min_jobs,
            retry_days=settings.grow_retry_days,
            delay=settings.grow_delay_seconds,
        )
        session.commit()
        return asdict(result)


@app.task(name="workers.tasks.scraping.detect_company")
def detect_company(url_or_name: str, name: str | None = None) -> dict[str, str] | None:
    """Resolve a careers URL or company name to a board and register it (§4.3)."""
    with http.client() as client:
        found = detection.detect(client, url_or_name)
    if found is None:
        return None

    ats, slug = found
    with SessionLocal() as session:
        registry.register(session, ats, slug, name or slug, registry.domain_of(url_or_name))
        session.commit()
    return {"ats_type": ats.value, "ats_slug": slug}


@app.task(name="workers.tasks.scraping.seed_registry")
def seed_registry() -> int:
    """Populate the registry with known boards so a fresh database has work to do."""
    with SessionLocal() as session:
        inserted = registry.seed_registry(session)
        session.commit()
    return inserted
