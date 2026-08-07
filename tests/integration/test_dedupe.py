"""M2's gate, against a real Postgres.

    "Run M1 + M2 together on an overlapping company -> assert the duplicate collapses to
     one row and the surviving row keeps the ATS apply URL."
    "Assert total unique job count increases vs. M1 alone."

The ATS side is produced by running the real M1 stage against a MockTransport board, so
the thing under test is the actual ingest output rather than a hand-built row. The
aggregator and feed sides are inserted directly: their own adapters are covered
elsewhere, and what matters here is the source string and the URL.
"""

from typing import Any

import httpx
import pytest
from db.models import Company, Job
from schemas.enums import AtsType
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from workers.scraping import dedupe, ingest

SLUG = "acme"
COMPANY = "Acme Inc"
TITLE = "Senior Software Engineer"
CITY = "San Francisco, CA"


def _board(*postings: dict[str, Any]) -> httpx.Client:
    payload = {"jobs": list(postings)}
    return httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(200, json=payload))
    )


def _posting(job_id: int, title: str = TITLE, location: str = CITY) -> dict[str, Any]:
    return {
        "id": job_id,
        "title": title,
        "company_name": COMPANY,
        "absolute_url": f"https://boards.greenhouse.io/{SLUG}/jobs/{job_id}",
        "location": {"name": location},
        "content": "Write software.",
        "first_published": "2026-07-30T06:59:38-04:00",
    }


@pytest.fixture
def company(session: Session) -> Company:
    row = Company(name=COMPANY, ats_type=AtsType.GREENHOUSE.value, ats_slug=SLUG)
    session.add(row)
    session.flush()
    return row


def _external(
    session: Session,
    source: str,
    *,
    company_name: str = "Acme",
    title: str = "Sr. Software Engineer",
    location: str = "San Francisco, California, United States",
    url: str = "https://www.linkedin.com/jobs/view/3912345",
    external_id: str = "1",
) -> Job:
    """One row from a non-ATS layer, inserted the way its own ingest would."""
    job = Job(
        source=source,
        external_id=f"{source}:{external_id}",
        title=title,
        company=company_name,
        company_id=None,
        location=location,
        locations=[location],
        url=url,
        raw_json={"id": external_id},
    )
    session.add(job)
    session.flush()
    return job


def _survivors(session: Session) -> list[Job]:
    """The deduped open pool — the entire interface M4 gets (§3.1)."""
    return list(
        session.scalars(
            select(Job)
            .where(Job.closed_at.is_(None), Job.canonical_id.is_(None))
            .order_by(Job.source)
        )
    )


def _count(session: Session) -> int:
    return session.scalar(select(func.count()).select_from(Job)) or 0


# ------------------------------------------------------------------------------ the gate


def test_an_overlapping_company_collapses_to_one_row_keeping_the_ats_url(
    session: Session, company: Company
) -> None:
    ingest.ingest_company(session, _board(_posting(1)), company)
    _external(session, "jobspy:linkedin")

    result = dedupe.dedupe_jobs(session)

    survivors = _survivors(session)
    assert len(survivors) == 1
    assert survivors[0].source == AtsType.GREENHOUSE.value
    assert survivors[0].url.startswith("https://boards.greenhouse.io/")
    assert result.duplicates == 1
    # Marked, not deleted. The aggregator's next pass must still find its conflict
    # target, or it re-inserts the row and the run rewrites rows forever.
    assert _count(session) == 2


def test_the_ats_row_wins_even_when_the_aggregator_arrived_first(
    session: Session, company: Company
) -> None:
    """Priority is a pure function of `source`, so ingest order cannot change the
    outcome. This is the half that bookkeeping would get wrong."""
    _external(session, "jobspy:linkedin")
    ingest.ingest_company(session, _board(_posting(1)), company)

    dedupe.dedupe_jobs(session)

    assert _survivors(session)[0].source == AtsType.GREENHOUSE.value


def test_a_feed_row_outranks_an_aggregator_row(session: Session) -> None:
    """Remotive carries the employer's own link far more often than LinkedIn does."""
    _external(session, "remotive", url="https://remotive.com/remote-jobs/1", external_id="1")
    _external(session, "jobspy:linkedin", external_id="2")

    dedupe.dedupe_jobs(session)

    survivors = _survivors(session)
    assert len(survivors) == 1
    assert survivors[0].source == "remotive"


def test_the_unique_open_count_rises_when_a_second_layer_lands(
    session: Session, company: Company
) -> None:
    """The third gate clause. One overlap collapses; genuinely new roles do not."""
    ingest.ingest_company(session, _board(_posting(1), _posting(2, title="Data Engineer")), company)
    dedupe.dedupe_jobs(session)
    m1_only = len(_survivors(session))

    _external(session, "jobspy:linkedin", external_id="1")  # overlaps posting 1
    _external(session, "jobspy:linkedin", title="Product Manager", external_id="2")
    _external(session, "remotive", title="SRE", external_id="3")
    dedupe.dedupe_jobs(session)

    assert m1_only == 2
    assert len(_survivors(session)) == 4


# ----------------------------------------------------------------------- writes only diffs


def test_a_second_pass_writes_nothing(session: Session, company: Company) -> None:
    """The M1 gate's invariant, applied to this stage. The target is a pure function of
    the open set, so a converged pass has nothing to write."""
    ingest.ingest_company(session, _board(_posting(1)), company)
    _external(session, "jobspy:linkedin")
    dedupe.dedupe_jobs(session)
    before = {job.id: job.updated_at for job in session.scalars(select(Job))}

    result = dedupe.dedupe_jobs(session)

    assert (result.keyed, result.duplicates, result.promoted) == (0, 0, 0)
    assert {job.id: job.updated_at for job in session.scalars(select(Job))} == before


# ---------------------------------------------------------------------------- the backfill


def test_rows_that_predate_the_pass_are_keyed_on_the_first_run(
    session: Session, company: Company
) -> None:
    """Ingest writes no key at all, so without this nothing would ever merge."""
    ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)
    assert all(job.dedupe_key is None for job in session.scalars(select(Job)))

    result = dedupe.dedupe_jobs(session)

    assert result.keyed == 2
    assert all(job.dedupe_key for job in session.scalars(select(Job)))


def test_an_edited_title_is_re_keyed_and_splits_the_group(
    session: Session, company: Company
) -> None:
    """`upsert` clears the key when a payload genuinely changes; the pass recomputes."""
    ingest.ingest_company(session, _board(_posting(1)), company)
    _external(session, "jobspy:linkedin")
    dedupe.dedupe_jobs(session)
    assert len(_survivors(session)) == 1

    ingest.ingest_company(session, _board(_posting(1, title="Staff Software Engineer")), company)
    result = dedupe.dedupe_jobs(session)

    assert result.keyed == 1
    assert result.promoted == 1
    assert len(_survivors(session)) == 2


# -------------------------------------------------------------------- interaction with close


def test_closing_the_canonical_promotes_the_duplicate(session: Session, company: Company) -> None:
    """The ATS posting vanishes and the aggregator copy is still listed, so it takes
    over — carrying its own redirect URL, which is all we have left."""
    ingest.ingest_company(session, _board(_posting(1)), company)
    _external(session, "jobspy:linkedin")
    dedupe.dedupe_jobs(session)

    ingest.ingest_company(session, _board(_posting(2, title="Data Engineer")), company)
    result = dedupe.dedupe_jobs(session)

    assert result.promoted == 1
    survivors = {job.source for job in _survivors(session)}
    assert "jobspy:linkedin" in survivors


def test_a_reposted_ats_row_takes_the_crown_back(session: Session, company: Company) -> None:
    ingest.ingest_company(session, _board(_posting(1)), company)
    _external(session, "jobspy:linkedin")
    dedupe.dedupe_jobs(session)
    ingest.ingest_company(session, _board(), company)  # empty board closes nothing
    ingest.close_missing(session, AtsType.GREENHOUSE.value, [], company.id)
    dedupe.dedupe_jobs(session)
    assert _survivors(session)[0].source == "jobspy:linkedin"

    ingest.ingest_company(session, _board(_posting(1)), company)
    dedupe.dedupe_jobs(session)

    survivors = _survivors(session)
    assert len(survivors) == 1
    assert survivors[0].source == AtsType.GREENHOUSE.value


def test_a_closed_duplicate_is_left_alone(session: Session, company: Company) -> None:
    """Every consumer already filters closed_at IS NULL, so clearing its canonical_id
    would be a write with no reader."""
    ingest.ingest_company(session, _board(_posting(1)), company)
    aggregator = _external(session, "jobspy:linkedin")
    dedupe.dedupe_jobs(session)
    # The pass writes through Core, so a held ORM object keeps the pre-update value —
    # same reason test_ingest.py re-reads rather than trusting its own references.
    session.refresh(aggregator)
    assert aggregator.canonical_id is not None

    ingest.close_missing(session, "jobspy:linkedin", [], None)
    result = dedupe.dedupe_jobs(session)

    session.refresh(aggregator)
    assert result.changed == 0
    assert aggregator.canonical_id is not None


# ------------------------------------------------------------------ false merges refused


def test_three_cities_of_one_title_stay_three_survivors(session: Session, company: Company) -> None:
    ingest.ingest_company(
        session,
        _board(
            _posting(1, location="San Francisco, CA"),
            _posting(2, location="New York, NY"),
            _posting(3, location="London, UK"),
        ),
        company,
    )

    dedupe.dedupe_jobs(session)

    assert len(_survivors(session)) == 3


def test_two_postings_from_one_board_are_never_merged(session: Session, company: Company) -> None:
    """§4.2 is about an aggregator row matching an ATS row, not about second-guessing a
    source's own listing. An employer posting the same title twice is opening two
    headcount, and its board's ids already say so.

    Caught against live Gopuff data: every same-source merge in a real run was a
    distinct opening at a distinct site.
    """
    ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)

    result = dedupe.dedupe_jobs(session)

    assert result.duplicates == 0
    assert len(_survivors(session)) == 2


def test_a_same_source_pair_still_absorbs_a_row_from_another_source(
    session: Session, company: Company
) -> None:
    """The rule must not accidentally protect the aggregator copy too."""
    ingest.ingest_company(session, _board(_posting(1), _posting(2)), company)
    _external(session, "jobspy:linkedin")

    dedupe.dedupe_jobs(session)

    survivors = _survivors(session)
    assert len(survivors) == 2
    assert {job.source for job in survivors} == {AtsType.GREENHOUSE.value}


def test_an_unkeyable_row_is_never_merged(session: Session, company: Company) -> None:
    """Two rows with blank companies must not collapse into each other just because
    both keys are NULL — each gets its own singleton partition."""
    _external(session, "remotive", company_name="", title="Engineer", external_id="1")
    _external(session, "jobicy", company_name="", title="Engineer", external_id="2")

    dedupe.dedupe_jobs(session)

    survivors = _survivors(session)
    assert len(survivors) == 2
    assert all(job.dedupe_key is None for job in survivors)
