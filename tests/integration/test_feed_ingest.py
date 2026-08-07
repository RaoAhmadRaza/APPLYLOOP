"""Layer 3 against a real Postgres: seed, run the stage, assert the row delta.

The feeds are driven through a fake module rather than the real eight. What is under
test here is `feed.ingest_feed` — the diff, and the three guards on the one destructive
statement in the stage — and a real adapter would only add its own field mapping to the
failure surface. The mappings are covered against recorded payloads in
tests/unit/test_feed_adapters.py.

`COMPLETE` is the flag worth being paranoid about: it decides whether a posting's
absence is allowed to close a row, and getting it wrong retires a whole feed's history
in one tick.
"""

from datetime import UTC, datetime, timedelta
from types import ModuleType, SimpleNamespace
from typing import Any

import httpx
import pytest
from db.models import Company, Event, Job
from schemas.enums import AtsType
from schemas.job import JobCreate
from sqlalchemy import func, select, update
from sqlalchemy.orm import Session
from workers.scraping import feed, ingest

SOURCE = "testfeed"


def _posting(job_id: int, title: str = "Engineer") -> dict[str, Any]:
    return {
        "id": job_id,
        "title": title,
        "company_name": "Acme Inc",
        "url": f"https://testfeed.test/jobs/{job_id}",
        "location": "Remote",
    }


def _feed(*postings: dict[str, Any], complete: bool = True) -> ModuleType:
    """A feed module returning exactly these postings."""

    def fetch(client: httpx.Client, pages: int = 1, delay: float = 0.0) -> list[dict[str, Any]]:
        return list(postings)

    def normalize(raw: dict[str, Any]) -> JobCreate:
        return JobCreate(
            source=SOURCE,
            external_id=f"{SOURCE}:{raw['id']}",
            title=raw["title"],
            company=raw["company_name"],
            location=raw["location"],
            locations=[raw["location"]],
            description=None,
            url=raw["url"],
            ats_type=None,
            posted_at=None,
            raw_json=raw,
        )

    return SimpleNamespace(  # type: ignore[return-value]
        SOURCE=SOURCE,
        HOME="https://testfeed.test",
        COMPLETE=complete,
        INTERVAL_HOURS=12,
        PAGES=1,
        fetch=fetch,
        normalize=normalize,
    )


def _client() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(200, json={})))


def _jobs(session: Session) -> list[Job]:
    return list(session.scalars(select(Job).order_by(Job.external_id)))


def _latest_event(session: Session) -> Event:
    return session.scalars(select(Event).order_by(Event.id.desc()).limit(1)).one()


# ------------------------------------------------------------------------- first pass


def test_first_pass_inserts_every_posting_with_no_company_id(session: Session) -> None:
    # Arrange
    source = _feed(_posting(1), _posting(2))

    # Act
    result = feed.ingest_feed(session, _client(), source)

    # Assert
    assert (result.fetched, result.inserted, result.updated, result.closed) == (2, 2, 0, 0)
    rows = _jobs(session)
    assert [row.external_id for row in rows] == [f"{SOURCE}:1", f"{SOURCE}:2"]
    # A feed names an employer we may hold no registry row for. `grow` fills these in.
    assert all(row.company_id is None for row in rows)
    assert all(row.closed_at is None for row in rows)


def test_the_run_event_records_the_row_counts(session: Session) -> None:
    """§8.2 wants rows-ingested-per-source-per-run without a new table."""
    feed.ingest_feed(session, _client(), _feed(_posting(1)))

    event = _latest_event(session)

    assert event.type == "feed.run"
    assert event.payload_json == {
        "feed": SOURCE,
        "fetched": 1,
        "inserted": 1,
        "updated": 0,
        "closed": 0,
    }


# ------------------------------------------------------------- only diffs, no duplicates


def test_second_identical_pass_writes_nothing(session: Session) -> None:
    """§6.3's invariant, for layer 3."""
    source = _feed(_posting(1), _posting(2))
    feed.ingest_feed(session, _client(), source)
    before = {row.external_id: row.updated_at for row in _jobs(session)}

    result = feed.ingest_feed(session, _client(), source)

    assert (result.inserted, result.updated, result.closed) == (0, 0, 0)
    assert {row.external_id: row.updated_at for row in _jobs(session)} == before
    assert session.scalar(select(func.count()).select_from(Job)) == 2


def test_a_changed_posting_updates_in_place(session: Session) -> None:
    feed.ingest_feed(session, _client(), _feed(_posting(1)))

    result = feed.ingest_feed(session, _client(), _feed(_posting(1, title="Staff Engineer")))

    assert (result.inserted, result.updated) == (0, 1)
    assert _jobs(session)[0].title == "Staff Engineer"
    assert session.scalar(select(func.count()).select_from(Job)) == 1


def test_a_repeated_posting_in_one_pass_does_not_raise(session: Session) -> None:
    """An offset-paginated feed returns the same posting twice whenever a new one is
    inserted at the top between two page requests. Postgres refuses an ON CONFLICT that
    would touch a row twice in one statement, so this would be a hard failure rather
    than a duplicate row."""
    result = feed.ingest_feed(session, _client(), _feed(_posting(1), _posting(1)))

    assert result.inserted == 1
    assert session.scalar(select(func.count()).select_from(Job)) == 1


# ------------------------------------------------------------------- closing, guarded


def test_a_complete_feed_closes_what_vanished(session: Session) -> None:
    feed.ingest_feed(session, _client(), _feed(_posting(1), _posting(2)))

    result = feed.ingest_feed(session, _client(), _feed(_posting(1)))

    assert result.closed == 1
    rows = {row.external_id: row for row in _jobs(session)}
    assert rows[f"{SOURCE}:1"].closed_at is None
    assert rows[f"{SOURCE}:2"].closed_at is not None


def test_a_paginated_feed_never_closes_by_absence(session: Session) -> None:
    """The guard that matters most. A posting missing from the pages we asked for may
    simply be on a page we did not ask for."""
    feed.ingest_feed(session, _client(), _feed(_posting(1), _posting(2), complete=False))

    result = feed.ingest_feed(session, _client(), _feed(_posting(1), complete=False))

    assert result.closed == 0
    assert all(row.closed_at is None for row in _jobs(session))


def test_an_empty_pass_closes_nothing(session: Session) -> None:
    """§3.7's most common real failure: a source that returns zero rows and raises
    nothing. Closing here would retire the feed's entire history on one glitch."""
    feed.ingest_feed(session, _client(), _feed(_posting(1), _posting(2)))

    result = feed.ingest_feed(session, _client(), _feed())

    assert (result.fetched, result.closed) == (0, 0)
    assert all(row.closed_at is None for row in _jobs(session))
    assert _latest_event(session).type == "feed.empty"


def test_a_volume_collapse_closes_nothing_and_says_so(session: Session) -> None:
    """A feed returning 2 postings where it returned 20 is broken, not empty. Alerting
    after the close would be alerting after the damage."""
    many = _feed(*[_posting(n) for n in range(20)])
    feed.ingest_feed(session, _client(), many)

    result = feed.ingest_feed(session, _client(), _feed(_posting(0), _posting(1)))

    assert result.closed == 0
    assert all(row.closed_at is None for row in _jobs(session))
    # The run event is still written after it — a suppressed pass is still a pass, and
    # §8.2 wants its counts either way.
    drop = session.scalars(
        select(Event).where(Event.type == "feed.volume_drop").order_by(Event.id.desc()).limit(1)
    ).one()
    assert drop.payload_json["fetched"] == 2
    assert drop.payload_json["previous"] == 20
    assert _latest_event(session).type == "feed.run"


def test_a_believable_dip_still_closes(session: Session) -> None:
    """The floor has to catch a collapse without blocking normal fluctuation."""
    feed.ingest_feed(session, _client(), _feed(*[_posting(n) for n in range(10)]))

    result = feed.ingest_feed(session, _client(), _feed(*[_posting(n) for n in range(8)]))

    assert result.closed == 2


def test_a_reposted_job_reopens(session: Session) -> None:
    source = _feed(_posting(1))
    feed.ingest_feed(session, _client(), source)
    feed.ingest_feed(session, _client(), _feed(_posting(2)))
    assert _jobs(session)[0].closed_at is not None

    feed.ingest_feed(session, _client(), _feed(_posting(1), _posting(2)))

    assert all(row.closed_at is None for row in _jobs(session))


# ---------------------------------------------------------------- the age backstop


def test_stale_rows_are_closed_by_age(session: Session) -> None:
    """The only way a paginated feed's rows ever close."""
    feed.ingest_feed(session, _client(), _feed(_posting(1), _posting(2), complete=False))
    session.execute(
        update(Job)
        .where(Job.external_id == f"{SOURCE}:1")
        .values(posted_at=datetime.now(UTC) - timedelta(days=100))
    )
    session.flush()

    closed = feed.close_stale(session, SOURCE, older_than_days=45)

    assert closed == 1
    rows = {row.external_id: row for row in _jobs(session)}
    assert rows[f"{SOURCE}:1"].closed_at is not None
    assert rows[f"{SOURCE}:2"].closed_at is None


# --------------------------------------------------------------- scopes stay disjoint


@pytest.fixture
def ats_job(session: Session) -> Job:
    """One layer-1 row, so the close statements can be shown not to reach each other."""
    company = Company(name="Acme Inc", ats_type=AtsType.GREENHOUSE.value, ats_slug="acme")
    session.add(company)
    session.flush()
    job = Job(
        source=AtsType.GREENHOUSE.value,
        external_id="acme:1",
        title="Engineer",
        company="Acme Inc",
        company_id=company.id,
        url="https://boards.greenhouse.io/acme/jobs/1",
        raw_json={"id": 1},
    )
    session.add(job)
    session.flush()
    return job


def test_a_feed_close_never_touches_an_ats_row(session: Session, ats_job: Job) -> None:
    """close_missing scopes a feed by source alone, with no company_id. That is only
    safe because the namespaces are disjoint."""
    feed.ingest_feed(session, _client(), _feed(_posting(1)))

    feed.ingest_feed(session, _client(), _feed(_posting(2)))

    session.refresh(ats_job)
    assert ats_job.closed_at is None


def test_an_ats_close_never_touches_a_feed_row(session: Session, ats_job: Job) -> None:
    feed.ingest_feed(session, _client(), _feed(_posting(1)))

    ingest.close_missing(session, AtsType.GREENHOUSE.value, [], ats_job.company_id)

    session.refresh(ats_job)
    assert ats_job.closed_at is not None
    assert all(row.closed_at is None for row in _jobs(session) if row.source == SOURCE)
