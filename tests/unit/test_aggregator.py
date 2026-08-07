"""Layer 2, against payloads recorded from real JobSpy runs.

The fixtures in tests/fixtures/aggregator/ were produced on 2026-08-07 by calling
`scrape_jobs` against Indeed and LinkedIn and dumping `df.head(3).to_json(...)` — the
same round trip `aggregator.to_rows` performs, so what these tests drive is exactly what
production sees.

The two sites are both here on purpose: Indeed populates `job_url_direct`, salary and a
description, while LinkedIn leaves `job_url_direct` null and returns an empty
description. Between them they cover the mapping's branches.

Offline. `scrape_jobs` is never called.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from schemas.enums import RemoteMode
from workers.scraping import aggregator

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "aggregator"


def load(site: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = json.loads((FIXTURES / f"jobspy_{site}.json").read_text())
    return rows


INDEED = load("indeed")
LINKEDIN = load("linkedin")
ALL_ROWS = [
    pytest.param(row, id=f"{row['site']}-{index}")
    for site in (INDEED, LINKEDIN)
    for index, row in enumerate(site)
]


# ------------------------------------------------------------------------- containment


def test_the_source_is_prefixed_so_the_layer_can_be_dropped_wholesale() -> None:
    """§11: isolate scraping so a takedown hits one module. With the prefix that is
    `DELETE FROM jobs WHERE source LIKE 'jobspy:%'` rather than a refactor."""
    assert aggregator.source_for("linkedin") == "jobspy:linkedin"
    assert all(
        aggregator.normalize(row).source.startswith(aggregator.SOURCE_PREFIX)  # type: ignore[union-attr]
        for row in INDEED
    )


def test_descriptions_are_not_fetched_per_job() -> None:
    """One extra request per job, and precisely what trips the rate limit. §3.5 says
    filter before you spend — M4 fetches descriptions for the shortlist."""
    assert aggregator.FETCH_DESCRIPTIONS is False


def test_results_wanted_stays_under_linkedins_page_threshold() -> None:
    """LinkedIn throttles around the tenth page per IP and a page is roughly 25."""
    assert aggregator.RESULTS_WANTED <= 250


# ---------------------------------------------------------------------------- the URL


def test_the_direct_employer_url_wins_when_the_site_exposes_one() -> None:
    """§7.4: always prefer the direct ATS URL. It is also the seam §4.3's reverse-index
    reads to grow the registry."""
    row = INDEED[0]
    assert row["job_url_direct"]

    assert aggregator.normalize(row).url == row["job_url_direct"]  # type: ignore[union-attr]


def test_the_listing_url_is_the_fallback() -> None:
    """LinkedIn returns job_url_direct as null on every row."""
    row = LINKEDIN[0]
    assert row["job_url_direct"] is None

    assert aggregator.normalize(row).url == row["job_url"]  # type: ignore[union-attr]


# ------------------------------------------------------------------------- the mapping


@pytest.mark.parametrize("row", ALL_ROWS)
def test_every_row_produces_a_namespaced_id_and_required_fields(row: dict[str, Any]) -> None:
    job = aggregator.normalize(row)

    assert job is not None
    assert job.external_id.startswith(f"{row['site']}:")
    assert job.external_id != f"{row['site']}:None"
    assert job.source == f"jobspy:{row['site']}"
    # A layer-2 row must never claim to be first-party: it could win a dedupe against
    # the real ATS row and cost the survivor its apply URL (§4.2).
    assert job.ats_type is None
    assert job.title
    assert job.company
    assert job.url.startswith("http")
    # §6.3: raw_json is exactly what the source returned.
    assert job.raw_json == row
    if job.posted_at is not None:
        assert job.posted_at.tzinfo is not None


def test_a_multipart_location_stays_one_place() -> None:
    """Splitting "Nashville, TN, US" would poison M4's && GIN filter with "TN" and "US"
    as though they were places."""
    job = aggregator.normalize(INDEED[0])

    assert job is not None
    assert job.locations == [INDEED[0]["location"]]
    assert len(job.locations) == 1


@pytest.mark.parametrize(
    ("is_remote", "expected"),
    [
        (True, RemoteMode.REMOTE),
        # Never ONSITE. False from an aggregator means "not detected" — JobSpy infers it
        # from a description keyword sniff — and mapping it to ONSITE would silently
        # drop remote jobs for remote-only users at M4's free hard filter.
        (False, None),
        (None, None),
    ],
)
def test_is_remote_false_means_unknown_not_onsite(
    is_remote: bool | None, expected: RemoteMode | None
) -> None:
    job = aggregator.normalize({**INDEED[0], "is_remote": is_remote})

    assert job is not None
    assert job.remote_mode is expected


def test_an_id_less_row_gets_a_stable_digest() -> None:
    """Google and Bayt return no id. A uuid would differ every run and repost the whole
    search as new rows."""
    row = {**INDEED[0], "id": None}

    first = aggregator.normalize(row)
    second = aggregator.normalize(row)

    assert first is not None and second is not None
    assert first.external_id == second.external_id
    assert first.external_id != f"{row['site']}:None"


@pytest.mark.parametrize("missing", ["site", "title", "company", "job_url"])
def test_a_row_missing_something_essential_is_dropped_not_raised(missing: str) -> None:
    """One bad row out of fifty is a bad row, not a failed run."""
    assert aggregator.normalize({**INDEED[0], missing: None}) is None


def test_an_empty_company_is_dropped() -> None:
    assert aggregator.normalize({**INDEED[0], "company": "   "}) is None


# ------------------------------------------------------- the DataFrame -> JSON round trip


def test_pandas_sentinels_survive_the_round_trip() -> None:
    """NaN, NaT and numpy scalars are none of them JSON serialisable, and all of them
    would land in raw_json (JSONB). This is the whole reason to_rows uses to_json."""
    pandas = pytest.importorskip("pandas")
    frame = pandas.DataFrame(
        [
            {
                "site": "indeed",
                "id": "in-1",
                "title": "Engineer",
                "company": "Acme",
                "job_url": "https://www.indeed.com/viewjob?jk=1",
                "job_url_direct": None,
                "location": "Austin, TX, US",
                "date_posted": pandas.Timestamp("2026-08-07"),
                "min_amount": float("nan"),
                "is_remote": True,
            }
        ]
    )

    rows = aggregator.to_rows(frame)

    assert rows[0]["min_amount"] is None
    assert isinstance(rows[0]["date_posted"], str)
    # The proof that matters: the result is serialisable, so it can reach JSONB.
    json.dumps(rows[0])

    job = aggregator.normalize(rows[0])
    assert job is not None
    assert job.posted_at is not None


# --------------------------------------------------------------------------- the proxy


def test_scrape_returns_nothing_without_a_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    """The interlock, at the layer below the task. `scrape_jobs` is not even imported —
    monkeypatching it to explode proves the short-circuit happens first."""

    def explode(**_kwargs: Any) -> Any:
        raise AssertionError("scrape_jobs called without a proxy")

    monkeypatch.setattr("jobspy.scrape_jobs", explode)

    assert aggregator.scrape("indeed", "software engineer", "United States", []) == []


def test_scrape_passes_the_proxies_through(monkeypatch: pytest.MonkeyPatch) -> None:
    """M2 gate: "JobSpy goes through the proxy". The other half — that the ATS layer and
    the feeds do not — is tests/unit/test_proxy_scope.py."""
    pandas = pytest.importorskip("pandas")
    seen: dict[str, Any] = {}

    def capture(**kwargs: Any) -> Any:
        seen.update(kwargs)
        return pandas.DataFrame([])

    monkeypatch.setattr("jobspy.scrape_jobs", capture)

    aggregator.scrape(
        "indeed",
        "software engineer",
        "United States",
        ["user:pw@a.test:8080", "user:pw@b.test:8080"],
    )

    assert seen["proxies"] == ["user:pw@a.test:8080", "user:pw@b.test:8080"]
    assert seen["site_name"] == ["indeed"]
    assert seen["linkedin_fetch_description"] is False
