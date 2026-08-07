"""M1 gate item 2: the second run writes only diffs, and removed roles are closed.

Seed a company, run the stage against a controlled board, assert the row delta. No
mocking of other stages — there is nothing to mock, per Part 10.

The board is a MockTransport so a test can remove or edit one posting between runs,
which is the whole behaviour under test and not something a live board will do on cue.
"""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from db.models import Company, Event, Job
from schemas.enums import AtsType, CompanyStatus
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from workers.scraping import ingest

SLUG = "acme"


def _board(*postings: dict[str, Any]) -> httpx.Client:
    """A Greenhouse board returning exactly these postings."""
    payload = {"jobs": list(postings)}
    return httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )


def _posting(job_id: int, title: str = "Engineer") -> dict[str, Any]:
    return {
        "id": job_id,
        "title": title,
        "company_name": "Acme Inc",
        "absolute_url": f"https://boards.greenhouse.io/{SLUG}/jobs/{job_id}",
        "location": {"name": "Remote"},
        "content": "&lt;p&gt;Write software.&lt;/p&gt;",
        "first_published": "2026-07-30T06:59:38-04:00",
        "updated_at": "2026-08-01T06:59:38-04:00",
    }


@pytest.fixture
def company(session: Session) -> Company:
    row = Company(name="Acme Inc", ats_type=AtsType.GREENHOUSE.value, ats_slug=SLUG)
    session.add(row)
    session.flush()
    return row


def _jobs(session: Session) -> list[Job]:
    return list(session.scalars(select(Job).order_by(Job.external_id)))


# ------------------------------------------------------------------- first run


def test_first_run_inserts_every_posting(session: Session, company: Company) -> None:
    # Arrange
    board = _board(_posting(1), _posting(2))

    # Act
    result = ingest.ingest_company(session, board, company)

    # Assert
    assert (result.fetched, result.inserted, result.updated, result.closed) == (2, 2, 0, 0)
    rows = _jobs(session)
    assert [row.external_id for row in rows] == [f"{SLUG}:1", f"{SLUG}:2"]
    assert all(row.company_id == company.id for row in rows)
    assert all(row.closed_at is None for row in rows)
    # Descriptions arrive as plain text, not Greenhouse's double-escaped HTML.
    assert rows[0].description == "Write software."


def test_a_board_that_repeats_a_posting_does_not_raise(session: Session, company: Company) -> None:
    """Workable's widget returns one entry per location, so a role posted in six cities
    comes back six times under one shortcode — verified live against lawnstarter, which
    returns 46 entries for 9 postings.

    Postgres refuses an ON CONFLICT DO UPDATE that would touch a row twice in one
    statement, so this was a hard failure rather than a duplicate row. None of M1's
    three seeded Workable boards repeat, which is why it never fired.
    """
    board = _board(_posting(1), _posting(1), _posting(2))

    result = ingest.ingest_company(session, board, company)

    assert result.fetched == 3
    assert result.inserted == 2
    assert [row.external_id for row in _jobs(session)] == [f"{SLUG}:1", f"{SLUG}:2"]


# ------------------------------------------------ gate item 2: only diffs, no duplicates


def test_second_identical_run_changes_nothing(session: Session, company: Company) -> None:
    """The gate, stated exactly. Not just "no duplicates" — no writes at all."""
    # Arrange
    ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)
    session.flush()
    before = {row.external_id: row.updated_at for row in _jobs(session)}

    # Act
    result = ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)

    # Assert
    assert (result.inserted, result.updated, result.closed) == (0, 0, 0)
    assert result.changed == 0
    assert {row.external_id: row.updated_at for row in _jobs(session)} == before


def test_second_run_creates_no_duplicate_rows(session: Session, company: Company) -> None:
    for _ in range(3):
        ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)

    assert session.scalar(select(func.count()).select_from(Job)) == 2


def test_only_the_edited_posting_is_updated(session: Session, company: Company) -> None:
    """One changed title must not rewrite the whole board."""
    # Arrange
    ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)
    session.flush()
    untouched_before = next(r for r in _jobs(session) if r.external_id == f"{SLUG}:2").updated_at

    # Act
    result = ingest.ingest_company(
        session, _board(_posting(1, title="Staff Engineer"), _posting(2)), company
    )

    # Assert
    assert (result.inserted, result.updated) == (0, 1)
    rows = {row.external_id: row for row in _jobs(session)}
    assert rows[f"{SLUG}:1"].title == "Staff Engineer"
    assert rows[f"{SLUG}:2"].updated_at == untouched_before


# ------------------------------------------------ gate item 2: removed roles marked closed


def test_removed_posting_is_closed(session: Session, company: Company) -> None:
    # Arrange
    ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)

    # Act — posting 2 is gone from the board.
    result = ingest.ingest_company(session, _board(_posting(1)), company)

    # Assert
    assert result.closed == 1
    rows = {row.external_id: row for row in _jobs(session)}
    assert rows[f"{SLUG}:2"].closed_at is not None
    assert rows[f"{SLUG}:1"].closed_at is None


def test_reposted_job_reopens_even_when_the_payload_is_identical(
    session: Session, company: Company
) -> None:
    """The `OR closed_at IS NOT NULL` arm of the upsert predicate.

    Without it the payload diff finds nothing to change and the row stays closed
    forever, so a role that came back would never reach matching again.
    """
    # Arrange
    ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)
    ingest.ingest_company(session, _board(_posting(1)), company)
    session.flush()
    assert next(r for r in _jobs(session) if r.external_id == f"{SLUG}:2").closed_at is not None

    # Act — byte-identical payload returns.
    result = ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)

    # Assert
    assert result.updated == 1
    assert next(r for r in _jobs(session) if r.external_id == f"{SLUG}:2").closed_at is None


def test_an_empty_board_closes_nothing(session: Session, company: Company) -> None:
    """§3.7's most common silent failure: markup changes, the scraper returns zero rows,
    nothing raises. Closing here would wipe this employer's entire job set."""
    # Arrange
    ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)

    # Act
    result = ingest.ingest_company(session, _board(), company)

    # Assert
    assert (result.fetched, result.closed) == (0, 0)
    assert all(row.closed_at is None for row in _jobs(session))
    assert session.scalar(select(Event.type).order_by(Event.id.desc())) == "ingest.empty"


def test_closing_is_scoped_to_one_company(session: Session, company: Company) -> None:
    """Scoped by company_id, not by the raw `company` text — two boards spelling a name
    differently must not close each other's rows."""
    # Arrange
    other = Company(name="Acme Inc", ats_type=AtsType.GREENHOUSE.value, ats_slug="acme-eu")
    session.add(other)
    session.flush()
    ingest.ingest_company(session, _board(_posting(1)), company)
    ingest.ingest_company(session, _board(_posting(9)), other)

    # Act — the first board empties out except for a new posting.
    ingest.ingest_company(session, _board(_posting(3)), company)

    # Assert
    rows = {row.external_id: row for row in _jobs(session)}
    assert rows["acme:1"].closed_at is not None
    assert rows["acme-eu:9"].closed_at is None


# ------------------------------------------------------------------ registry bookkeeping


def test_success_stamps_the_registry_and_logs_row_counts(
    session: Session, company: Company
) -> None:
    """§8.2 wants rows-ingested-per-source-per-run without a new table."""
    # Arrange
    company.consecutive_failures = 3
    result = ingest.ingest_company(session, _board(_posting(1)), company)

    # Act
    ingest.record_success(session, company, result)

    # Assert
    assert company.consecutive_failures == 0
    assert company.last_seen_ok is not None
    assert company.jobs_last_run is not None
    event = session.scalars(select(Event).order_by(Event.id.desc())).first()
    assert event is not None
    assert event.type == "ingest.run"
    assert event.payload_json == {
        "ats": AtsType.GREENHOUSE.value,
        "slug": SLUG,
        "fetched": 1,
        "inserted": 1,
        "updated": 0,
        "closed": 0,
    }


def test_repeated_failure_retires_the_slug(session: Session, company: Company) -> None:
    """§4.3: auto-retire slugs that 404 for N runs. One blip must not be enough."""
    for _ in range(ingest.RETIRE_AFTER_FAILURES - 1):
        ingest.record_failure(session, company, "404 Not Found")
    assert company.status == CompanyStatus.ACTIVE.value

    ingest.record_failure(session, company, "404 Not Found")

    assert company.status == CompanyStatus.RETIRED.value
    assert company.consecutive_failures == ingest.RETIRE_AFTER_FAILURES


# ------------------------------------------------------- smartrecruiters detail fetching


def test_smartrecruiters_fetches_details_only_for_new_postings(session: Session) -> None:
    """The N+1 containment: a second run with no new postings makes no detail calls."""
    # Arrange
    company = Company(name="Visa", ats_type=AtsType.SMARTRECRUITERS.value, ats_slug=SLUG)
    session.add(company)
    session.flush()

    listing = {
        "id": "744",
        "name": "Sr. Manager",
        "company": {"name": "Visa"},
        "location": {"fullLocation": "Austin, TX", "remote": False, "hybrid": False},
        "releasedDate": "2026-06-24T10:00:11.853Z",
    }
    detail_calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/744"):
            detail_calls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    **listing,
                    "postingUrl": "https://jobs.smartrecruiters.com/Visa/744-sr-manager",
                    "jobAd": {"sections": {"jobDescription": {"text": "<p>Lead a team.</p>"}}},
                },
            )
        return httpx.Response(
            200, json={"offset": 0, "limit": 100, "totalFound": 1, "content": [listing]}
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))

    # Act
    ingest.ingest_company(session, client, company)
    second = ingest.ingest_company(session, client, company)

    # Assert
    assert len(detail_calls) == 1
    job = session.scalars(select(Job)).one()
    assert job.description == "Lead a team."
    assert job.url == "https://jobs.smartrecruiters.com/Visa/744-sr-manager"
    # Regression guard: merging the detail payload back into raw_json would make the
    # next run's diff fire on every row forever — and overwrite the description with the
    # listing's NULL. raw_json must keep holding exactly what the list endpoint returns.
    assert second.changed == 0
    assert job.raw_json == listing


# ------------------------------------------------------------------------ field fidelity


def test_posted_at_survives_as_an_aware_utc_instant(session: Session, company: Company) -> None:
    ingest.ingest_company(session, _board(_posting(1)), company)

    posted_at = session.scalars(select(Job.posted_at)).one()

    assert posted_at is not None
    assert posted_at.astimezone(UTC) == datetime(2026, 7, 30, 10, 59, 38, tzinfo=UTC)
