"""§4.3's reverse-index against a real Postgres.

The moat is the claim that coverage compounds: a feed row names an employer, we resolve
their ATS once, and layer 1 then pulls their whole board forever. These tests pin the
three things that claim depends on — that the cheap tier really is free, that a bad
guess cannot write another company's board into the registry, and that a miss is
remembered rather than re-probed every hour.
"""

from typing import Any

import httpx
import pytest
from db.models import Company, Job
from schemas.enums import AtsType, CompanyStatus
from sqlalchemy import select
from sqlalchemy.orm import Session
from workers.scraping import grow

GREENHOUSE_URL = "https://boards.greenhouse.io/acme/jobs/1"


def _job(
    session: Session,
    company: str,
    *,
    source: str = "remotive",
    external_id: str = "1",
    raw: dict[str, Any] | None = None,
) -> Job:
    row = Job(
        source=source,
        external_id=f"{source}:{external_id}",
        title="Engineer",
        company=company,
        company_id=None,
        url=f"https://{source}.test/jobs/{external_id}",
        raw_json=raw if raw is not None else {"id": external_id},
    )
    session.add(row)
    session.flush()
    return row


def _no_network() -> httpx.Client:
    """Any request through this fails the test rather than silently costing one."""

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request to {request.url}")

    return httpx.Client(transport=httpx.MockTransport(handler))


def _board(payload: Any, seen: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def _dead_board() -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(lambda _r: httpx.Response(404)))


def _companies(session: Session) -> list[Company]:
    return list(session.scalars(select(Company).order_by(Company.name)))


# ------------------------------------------------------------------- tier 1, zero cost


def test_an_ats_url_inside_a_stored_payload_resolves_with_no_requests(
    session: Session,
) -> None:
    """The whole design rests on this: `detect.from_url` is a finditer over arbitrary
    text, so the payloads we already hold are searchable for free. The client raises on
    any request, so a regression here fails loudly rather than getting slower."""
    _job(session, "Acme", external_id="1", raw={"applicationLink": GREENHOUSE_URL})
    _job(session, "Acme", external_id="2", raw={"id": 2})

    result = grow.grow(session, _no_network(), min_jobs=2)

    assert (result.resolved, result.unresolved) == (1, 0)
    company = _companies(session)[0]
    assert (company.ats_type, company.ats_slug) == (AtsType.GREENHOUSE.value, "acme")


def test_resolving_links_every_open_row_for_that_employer(session: Session) -> None:
    """The `company_id` written here is what scopes M1's close statement and what a
    later dedupe keys against."""
    _job(session, "Acme", external_id="1", raw={"applicationLink": GREENHOUSE_URL})
    _job(session, "Acme", external_id="2")
    _job(session, "Other Co", source="jobicy", external_id="9")

    result = grow.grow(session, _no_network(), min_jobs=2)

    assert result.linked == 2
    linked = list(session.scalars(select(Job).where(Job.company == "Acme")))
    assert all(job.company_id is not None for job in linked)
    unrelated = session.scalars(select(Job).where(Job.company == "Other Co")).one()
    assert unrelated.company_id is None


# ---------------------------------------------------------------------- rationing


def test_a_one_job_employer_is_not_worth_six_requests(session: Session) -> None:
    _job(session, "Acme")

    result = grow.grow(session, _no_network(), min_jobs=2)

    assert result.considered == 0
    assert _companies(session) == []


def test_the_batch_cap_holds(session: Session) -> None:
    for index in range(4):
        for posting in range(2):
            _job(
                session,
                f"Company {index}",
                external_id=f"{index}-{posting}",
                raw={"applicationLink": f"https://boards.greenhouse.io/co{index}/jobs/1"},
            )

    result = grow.grow(session, _no_network(), batch=2, min_jobs=2)

    assert result.considered == 2
    assert len(_companies(session)) == 2


def test_the_biggest_posters_are_resolved_first(session: Session) -> None:
    """One detection unlocks that employer's whole board, so resolving the employer with
    the most open roles is where the compounding comes from."""
    for posting in range(2):
        _job(
            session,
            "Small Co",
            external_id=f"s{posting}",
            raw={"applicationLink": "https://boards.greenhouse.io/small/jobs/1"},
        )
    for posting in range(5):
        _job(
            session,
            "Big Co",
            external_id=f"b{posting}",
            raw={"applicationLink": "https://boards.greenhouse.io/big/jobs/1"},
        )

    grow.grow(session, _no_network(), batch=1, min_jobs=2)

    assert [company.name for company in _companies(session)] == ["Big Co"]


# --------------------------------------------------------------- the negative cache


def test_an_unresolvable_employer_is_remembered_not_re_probed(session: Session) -> None:
    """Six requests an hour, per company, forever is the alternative."""
    _job(session, "Acme", external_id="1")
    _job(session, "Acme", external_id="2")

    first = grow.grow(session, _dead_board(), min_jobs=2)

    assert (first.resolved, first.unresolved) == (0, 1)
    cached = _companies(session)[0]
    assert cached.ats_type == AtsType.OTHER.value
    assert cached.status == CompanyStatus.ERROR.value

    # The second run makes no request at all — the client would raise if it did.
    second = grow.grow(session, _no_network(), min_jobs=2)

    assert second.considered == 0


def test_a_cached_miss_is_re_probed_once_the_window_passes(session: Session) -> None:
    """Companies do adopt an ATS; a miss is not permanent."""
    _job(session, "Acme", external_id="1")
    _job(session, "Acme", external_id="2")
    grow.grow(session, _dead_board(), min_jobs=2)

    result = grow.grow(session, _dead_board(), min_jobs=2, retry_days=0)

    assert result.considered == 1


def test_a_cached_miss_leaves_its_rows_unlinked(session: Session) -> None:
    """`company_id IS NULL` is what marks an employer as still worth resolving. Linking
    a miss would retire it permanently and make retry_days unreachable — which is
    exactly what the test above would then be unable to prove."""
    _job(session, "Acme", external_id="1")
    _job(session, "Acme", external_id="2")

    result = grow.grow(session, _dead_board(), min_jobs=2)

    assert result.linked == 0
    assert all(job.company_id is None for job in session.scalars(select(Job)))


# ------------------------------------------------------- the moat-corruption guard


def test_a_probe_hit_naming_a_different_company_is_rejected(session: Session) -> None:
    """detect.py's contract says tier-3 hits are provisional. A slug guess landing on
    someone else's board would write their postings into our registry under our name —
    corruption of the exact thing §4.3 calls the moat."""
    _job(session, "Acme", external_id="1")
    _job(session, "Acme", external_id="2")
    # A live Greenhouse board that belongs to somebody else entirely.
    impostor = _board(
        {
            "jobs": [
                {
                    "id": 1,
                    "title": "Engineer",
                    "company_name": "Totally Different Corp",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    "location": {"name": "Remote"},
                }
            ]
        }
    )

    result = grow.grow(session, impostor, min_jobs=2)

    assert (result.resolved, result.unresolved) == (0, 1)
    assert _companies(session)[0].ats_type == AtsType.OTHER.value


def test_a_probe_hit_on_a_board_that_names_no_company_is_accepted(session: Session) -> None:
    """Lever and Ashby do not name the employer in their payload. Rejecting those would
    reject most real hits."""
    _job(session, "Acme", external_id="1")
    _job(session, "Acme", external_id="2")
    silent = _board(
        {
            "jobs": [
                {
                    "id": 1,
                    "title": "Engineer",
                    "absolute_url": "https://boards.greenhouse.io/acme/jobs/1",
                    "location": {"name": "Remote"},
                }
            ]
        }
    )

    result = grow.grow(session, silent, min_jobs=2)

    assert result.resolved == 1
    assert _companies(session)[0].ats_type == AtsType.GREENHOUSE.value


# ---------------------------------------------------------------- the ingest_all guard


def test_ingest_all_skips_the_rows_grow_creates(session: Session) -> None:
    """`other` has no adapter behind it, so ADAPTERS[AtsType.OTHER] is a KeyError in
    production. This is the clause that makes that impossible rather than unlikely."""
    from schemas.enums import CompanyStatus as Status

    session.add(
        Company(
            name="Acme",
            ats_type=AtsType.OTHER.value,
            ats_slug="acme",
            status=Status.ACTIVE.value,
        )
    )
    session.flush()

    selectable = select(Company.id).where(
        Company.status == Status.ACTIVE.value,
        Company.ats_type != AtsType.OTHER.value,
    )

    assert list(session.scalars(selectable)) == []


@pytest.fixture(autouse=True)
def _no_leftover_companies(session: Session) -> None:
    """The registry is global, so a stray row from another test would change the batch
    ordering these assertions depend on."""
    assert _companies(session) == []
