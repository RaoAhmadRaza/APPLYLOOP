"""§4.3's reverse-index: the half of the registry that grows itself.

Seeding the slug registry by hand gets you a few thousand companies. Growing it is what
makes it a moat, and the mechanism is already sitting in the database: every feed and
aggregator row names an employer we do not have a board for. Resolve that employer's
ATS once and layer 1 pulls their *entire* board on the next `ingest_all` — one detection
buys every role they will ever post.

That is why "unique job count rises" compounds instead of plateauing, and why candidates
are ordered by open-role count: resolving the biggest posters first unlocks the most.

Three tiers of cost, and the cheap one does most of the work:

  1. **Zero requests.** `detect.from_url` is a `finditer` over arbitrary text — which is
     exactly what `detect.from_page` does to a fetched HTML body. Point it at the
     payloads we already stored and Himalayas' `applicationLink`, RemoteOK's
     `apply_url`, JobSpy's `job_url_direct` and any apply link embedded in an HTML
     description all resolve for free, with no per-source code in here.
  2. **Six requests**, rationed. Only what tier 1 missed, only for employers with more
     than one open role, capped per run and paced.
  3. **One more request** to verify. `detect.py`'s own docstring says a slug guess can
     match a *different* company's board and puts verification on the caller — and
     registering the wrong board writes another employer's postings into ours, which is
     corruption of the exact thing §4.3 calls the moat.

An employer we cannot resolve is remembered rather than re-probed every hour: it gets a
real registry row with `ats_type='other'` and `status='error'`. Both values already
exist and both CHECK constraints already accept them, so the negative cache needs no new
table, no new column and no migration. "No ATS we could find" is a legitimate answer
about a company, and the row still carries the name for later.
"""

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
from db.models import Company, Job
from schemas.enums import AtsType, CompanyStatus
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from workers.scraping import ADAPTERS, detect, registry

# How many stored payloads tier 1 reads per employer. Three is enough to catch a feed
# that only sometimes carries an apply link, and bounds the JSON we serialise.
PAYLOAD_SAMPLE = 3


@dataclass(frozen=True)
class Candidate:
    name: str
    jobs: int
    payloads: list[dict[str, Any]]


@dataclass(frozen=True)
class GrowResult:
    considered: int
    resolved: int
    unresolved: int
    linked: int

    @property
    def changed(self) -> int:
        return self.resolved + self.unresolved


def grow(
    session: Session,
    client: httpx.Client,
    *,
    batch: int = 25,
    min_jobs: int = 2,
    retry_days: int = 30,
    delay: float = 0.0,
) -> GrowResult:
    """Resolve a batch of unlinked employers and link their rows. Commits nothing."""
    candidates = unresolved_companies(session, batch, min_jobs, retry_days)
    resolved = 0
    unresolved = 0
    linked = 0

    for candidate in candidates:
        found = resolve(client, candidate, delay)
        if found is None:
            _remember_miss(session, candidate)
            unresolved += 1
            # Deliberately NOT linked. `company_id IS NULL` is what marks an employer as
            # still worth resolving, so linking a miss would retire it permanently — and
            # companies do adopt an ATS later. The `other` row is a dated note saying
            # "we looked", and `retry_days` is what decides when to look again.
            continue

        ats, slug = found
        company = registry.register(session, ats, slug, candidate.name, None)
        resolved += 1
        linked += _link(session, company, candidate.name)

    return GrowResult(
        considered=len(candidates), resolved=resolved, unresolved=unresolved, linked=linked
    )


def unresolved_companies(
    session: Session, batch: int, min_jobs: int, retry_days: int
) -> list[Candidate]:
    """Employers with open rows and no registry link, biggest posters first.

    `company_id IS NULL AND closed_at IS NULL` *is* the definition of "not yet
    reverse-indexed". It needs no new state, it covers layer 3 today and layer 2 the day
    JobSpy lands, and it empties itself as companies resolve.
    """
    rows = session.execute(
        select(Job.company, func.count().label("jobs"))
        .where(Job.company_id.is_(None), Job.closed_at.is_(None))
        .group_by(Job.company)
        .having(func.count() >= min_jobs)
        .order_by(func.count().desc(), Job.company)
        # Over-fetch, because cached misses are filtered in Python below and would
        # otherwise eat the batch.
        .limit(batch * 4)
    ).all()

    fresh = _drop_cached_misses(session, [row.company for row in rows], retry_days)
    return [
        Candidate(name=row.company, jobs=row.jobs, payloads=_payloads(session, row.company))
        for row in rows
        if row.company in fresh
    ][:batch]


def _drop_cached_misses(session: Session, names: list[str], retry_days: int) -> set[str]:
    """Remove employers we already failed to resolve inside the retry window.

    Filtered in Python rather than SQL so `detect.slugify`'s rules are not duplicated as
    a Postgres expression that could silently drift from them.
    """
    if not names:
        return set()

    by_slug = {detect.slugify(name): name for name in names}
    cutoff = datetime.now(UTC) - timedelta(days=retry_days)
    cached = session.execute(
        select(Company.ats_slug, Company.jobs_last_run).where(
            Company.ats_type == AtsType.OTHER.value,
            Company.ats_slug.in_(list(by_slug)),
        )
    ).all()

    stale = {
        by_slug[row.ats_slug]
        for row in cached
        if row.jobs_last_run is not None and row.jobs_last_run > cutoff
    }
    return set(names) - stale


def _payloads(session: Session, name: str) -> list[dict[str, Any]]:
    return list(
        session.scalars(
            select(Job.raw_json)
            .where(Job.company == name, Job.closed_at.is_(None))
            .order_by(Job.created_at.desc())
            .limit(PAYLOAD_SAMPLE)
        )
    )


def resolve(client: httpx.Client, candidate: Candidate, delay: float) -> tuple[AtsType, str] | None:
    """Tier 1 over what we already hold, then a rationed, verified probe."""
    for payload in candidate.payloads:
        found = detect.from_url(json.dumps(payload))
        if found:
            return found

    # Paced here and not in the caller's loop: tier 1 costs nothing, so only a candidate
    # that actually reaches the probe should slow the run down.
    if delay:
        time.sleep(delay)
    probed = detect.probe(client, detect.slugify(candidate.name))
    if probed is None:
        return None
    return probed if _board_names_this_company(client, probed, candidate.name) else None


def _board_names_this_company(client: httpx.Client, found: tuple[AtsType, str], name: str) -> bool:
    """A slug guess can land on a different company's board.

    Greenhouse, Recruitee and SmartRecruiters all name the employer in their payload and
    `normalize` surfaces it, falling back to whatever we passed in when the payload is
    silent — so this rejects a mismatch and waves through a board that never claimed a
    name. One extra request, and only on a hit.

    This lives here rather than in detect.py because detect.py's contract already says
    tier-3 hits are provisional and the caller is expected to verify.
    """
    ats, slug = found
    adapter = ADAPTERS[ats]
    sentinel = "\x00unclaimed"
    try:
        postings = adapter.fetch(client, slug)
        job = adapter.normalize(postings[0], sentinel, slug)
    except (httpx.HTTPError, IndexError, KeyError, TypeError, ValueError):
        return False

    if job.company == sentinel:
        return True
    return detect.slugify(job.company) == detect.slugify(name)


def _remember_miss(session: Session, candidate: Candidate) -> Company:
    """Negative cache, as a real registry row rather than a new table.

    `jobs_last_run` is reused as the retry clock because its meaning — "when this
    company was last processed" — is already exactly right, and because `register`'s
    Core upsert never bumps `updated_at` (the mixin's onupdate is Python-side), so a
    timestamp read from there would be frozen and the row re-probed forever.
    """
    company = registry.register(
        session, AtsType.OTHER, detect.slugify(candidate.name), candidate.name, None
    )
    company.status = CompanyStatus.ERROR.value
    company.jobs_last_run = datetime.now(UTC)
    company.consecutive_failures += 1
    session.flush()
    return company


def _link(session: Session, company: Company, name: str) -> int:
    """Attach this employer's unlinked rows to the registry row.

    Exact name match only. Two feeds spelling one employer differently is §4.2's
    problem, and the `company_id` written here is what a later dedupe keys against.
    """
    linked = session.scalars(
        update(Job)
        .where(Job.company_id.is_(None), Job.company == name)
        .values(company_id=company.id, updated_at=func.now())
        .returning(Job.id)
    ).all()
    session.flush()
    return len(linked)
