"""Every adapter, against payloads recorded from its real board.

The fixtures in tests/fixtures/ats/ are verbatim recordings (trimmed to a few postings)
from live endpoints — vercel/Greenhouse, leverdemo/Lever, Ashby/Ashby,
blueground/Workable, Visa/SmartRecruiters, channable/Recruitee. Assertions name the
*source* field on each side, so they pin the mapping rather than restating it.

`fetch()` runs through httpx.MockTransport, which ships with httpx — no respx, no vcr.
"""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from schemas.enums import AtsType, RemoteMode
from workers.scraping import ADAPTERS
from workers.scraping.adapters import (
    ashby,
    greenhouse,
    lever,
    recruitee,
    smartrecruiters,
    workable,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ats"
SLUG = "acme"
COMPANY = "Acme Inc"


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def mock_client(payload: Any, seen: list[httpx.Request] | None = None) -> httpx.Client:
    """A client that answers every request with `payload` and records what was asked."""

    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


# --------------------------------------------------------------------------- registry


def test_registry_covers_every_layer_one_provider() -> None:
    """§4.1 names six. A seventh AtsType with no adapter would silently never ingest."""
    assert set(ADAPTERS) == {
        AtsType.GREENHOUSE,
        AtsType.LEVER,
        AtsType.ASHBY,
        AtsType.WORKABLE,
        AtsType.SMARTRECRUITERS,
        AtsType.RECRUITEE,
    }


@pytest.mark.parametrize("ats", sorted(ADAPTERS, key=str))
def test_adapter_source_matches_its_ats_type(ats: AtsType) -> None:
    """`jobs.source` and `jobs.ats_type` must agree, or the close statement scopes wrong."""
    module = ADAPTERS[ats]
    assert ats.value == module.SOURCE
    assert module.ATS is ats


# ------------------------------------------------------------------------- greenhouse


def test_greenhouse_fetch_asks_for_content_and_unwraps_jobs() -> None:
    seen: list[httpx.Request] = []
    body = load("greenhouse")

    postings = greenhouse.fetch(mock_client(body, seen), SLUG)

    assert str(seen[0].url) == f"{greenhouse.BASE_URL}/{SLUG}/jobs?content=true"
    assert postings == body["jobs"]


def test_greenhouse_normalize_maps_every_field() -> None:
    raw = load("greenhouse")["jobs"][0]

    job = greenhouse.normalize(raw, COMPANY, SLUG)

    assert job.external_id == f"{SLUG}:{raw['id']}"
    assert job.title == raw["title"]
    assert job.company == raw["company_name"]
    assert job.url == raw["absolute_url"]
    assert job.location == raw["location"]["name"]
    assert job.locations == [raw["location"]["name"]]
    assert job.ats_type is AtsType.GREENHOUSE
    assert job.raw_json == raw


def test_greenhouse_posted_at_uses_first_published_not_updated_at() -> None:
    """The docs call `first_published` detail-only; it is in the list response. Using
    `updated_at` instead would report every edited posting as freshly posted."""
    raw = load("greenhouse")["jobs"][0]

    job = greenhouse.normalize(raw, COMPANY, SLUG)

    assert job.posted_at is not None
    assert job.posted_at.isoformat() != raw["updated_at"]
    assert job.posted_at.date().isoformat() == raw["first_published"][:10]


def test_greenhouse_remote_mode_is_null_because_the_payload_says_nothing() -> None:
    """Greenhouse carries no remote signal. Inferring one from the location string
    would be a guess, and NULL already means "the source did not say"."""
    for raw in load("greenhouse")["jobs"]:
        assert greenhouse.normalize(raw, COMPANY, SLUG).remote_mode is None


def test_greenhouse_description_is_plain_text() -> None:
    raw = load("greenhouse")["jobs"][0]

    description = greenhouse.normalize(raw, COMPANY, SLUG).description

    assert description
    assert "<p>" not in description
    assert "&lt;" not in description


# ------------------------------------------------------------------------------ lever


def test_lever_fetch_handles_a_bare_array_response() -> None:
    """Lever returns a list, not an envelope. Indexing ["jobs"] would raise."""
    seen: list[httpx.Request] = []
    body = load("lever")

    postings = lever.fetch(mock_client(body, seen), SLUG)

    assert str(seen[0].url) == f"{lever.BASE_URL}/{SLUG}?mode=json"
    assert postings == body


def test_lever_normalize_maps_text_to_title_and_hosted_url() -> None:
    raw = load("lever")[0]

    job = lever.normalize(raw, COMPANY, SLUG)

    assert job.title == raw["text"]
    assert job.url == raw["hostedUrl"]
    # Lever's payload never names the employer, so the registry row is the only source.
    assert job.company == COMPANY
    assert job.location == raw["categories"]["location"]
    assert job.locations == raw["categories"]["allLocations"]


def test_lever_created_at_is_epoch_milliseconds() -> None:
    raw = load("lever")[0]

    job = lever.normalize(raw, COMPANY, SLUG)

    assert job.posted_at is not None
    assert job.posted_at.timestamp() == pytest.approx(raw["createdAt"] / 1000)


def test_lever_description_includes_the_list_sections() -> None:
    """`descriptionPlain` alone drops qualifications and duties — most of what M4 embeds."""
    raw = next(r for r in load("lever") if r.get("lists"))

    description = lever.normalize(raw, COMPANY, SLUG).description

    assert description
    assert len(description) > len(raw["descriptionPlain"])
    for section in raw["lists"]:
        assert section["text"] in description


@pytest.mark.parametrize(
    ("workplace_type", "expected"),
    [
        ("remote", RemoteMode.REMOTE),
        ("hybrid", RemoteMode.HYBRID),
        ("on-site", RemoteMode.ONSITE),
        ("unspecified", None),
        (None, None),
    ],
)
def test_lever_workplace_type_mapping(
    workplace_type: str | None, expected: RemoteMode | None
) -> None:
    raw = {**load("lever")[0], "workplaceType": workplace_type}

    assert lever.normalize(raw, COMPANY, SLUG).remote_mode is expected


# ------------------------------------------------------------------------------ ashby


def test_ashby_fetch_unwraps_jobs_and_requests_compensation() -> None:
    seen: list[httpx.Request] = []
    body = load("ashby")

    postings = ashby.fetch(mock_client(body, seen), SLUG)

    assert str(seen[0].url) == f"{ashby.BASE_URL}/{SLUG}?includeCompensation=true"
    assert len(postings) == len(body["jobs"])


def test_ashby_fetch_drops_unlisted_postings() -> None:
    """isListed=False is a role the employer chose not to publish."""
    body = load("ashby")
    body["jobs"][0]["isListed"] = False

    postings = ashby.fetch(mock_client(body), SLUG)

    assert len(postings) == len(body["jobs"]) - 1


def test_ashby_normalize_folds_secondary_locations_into_the_array() -> None:
    """This is the provider that makes `jobs.locations` an array rather than a scalar."""
    raw = next(r for r in load("ashby")["jobs"] if r.get("secondaryLocations"))

    job = ashby.normalize(raw, COMPANY, SLUG)

    assert job.location == raw["location"]
    assert job.locations[0] == raw["location"]
    for secondary in raw["secondaryLocations"]:
        assert secondary["location"] in job.locations


def test_ashby_prefers_workplace_type_over_the_is_remote_boolean() -> None:
    """isRemote cannot express hybrid; workplaceType can."""
    raw = {**load("ashby")["jobs"][0], "isRemote": True, "workplaceType": "Hybrid"}

    assert ashby.normalize(raw, COMPANY, SLUG).remote_mode is RemoteMode.HYBRID


def test_ashby_posted_at_uses_published_at() -> None:
    raw = load("ashby")["jobs"][0]

    job = ashby.normalize(raw, COMPANY, SLUG)

    assert job.posted_at is not None
    assert job.posted_at.date().isoformat() == raw["publishedAt"][:10]


# --------------------------------------------------------------------------- workable


def test_workable_external_id_uses_shortcode_because_id_is_null() -> None:
    """Verified live: every Workable job has `"id": null`. Keying on it would collide
    every posting onto one row."""
    raw = load("workable")["jobs"][0]
    assert raw.get("id") is None

    job = workable.normalize(raw, COMPANY, SLUG)

    assert job.external_id == f"{SLUG}:{raw['shortcode']}"


def test_workable_fetch_requests_details_and_unwraps_jobs() -> None:
    seen: list[httpx.Request] = []
    body = load("workable")

    postings = workable.fetch(mock_client(body, seen), SLUG)

    assert str(seen[0].url) == f"{workable.BASE_URL}/{SLUG}?details=true"
    assert postings == body["jobs"]


def test_workable_builds_location_from_the_flat_city_state_country() -> None:
    raw = load("workable")["jobs"][0]

    job = workable.normalize(raw, COMPANY, SLUG)

    assert job.location is not None
    assert raw["city"] in job.location
    assert raw["country"] in job.location
    assert job.locations


@pytest.mark.parametrize(
    ("telecommuting", "expected"),
    # False must NOT become ONSITE: v1's single boolean collapses on-site and hybrid,
    # and mislabelling every hybrid role is the flattening migration 0003 removed.
    [(True, RemoteMode.REMOTE), (False, None)],
)
def test_workable_telecommuting_never_asserts_onsite(
    telecommuting: bool, expected: RemoteMode | None
) -> None:
    raw = {**load("workable")["jobs"][0], "telecommuting": telecommuting}

    assert workable.normalize(raw, COMPANY, SLUG).remote_mode is expected


# ---------------------------------------------------------------------- smartrecruiters


def test_smartrecruiters_fetch_pages_until_total_found() -> None:
    """The only paginated provider. Stopping after page one silently truncates a board."""
    body = load("smartrecruiters")
    page_size = 2
    postings = [{**body["content"][0], "id": str(i)} for i in range(5)]
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        offset = int(request.url.params["offset"])
        window = postings[offset : offset + page_size]
        return httpx.Response(
            200,
            json={
                "offset": offset,
                "limit": page_size,
                "totalFound": len(postings),
                "content": window,
            },
        )

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        monkey = smartrecruiters.PAGE_SIZE
        try:
            smartrecruiters.PAGE_SIZE = page_size
            fetched = smartrecruiters.fetch(client, SLUG)
        finally:
            smartrecruiters.PAGE_SIZE = monkey

    assert [p["id"] for p in fetched] == ["0", "1", "2", "3", "4"]
    assert len(seen) == 3


def test_smartrecruiters_list_response_carries_no_description() -> None:
    """The reason this adapter needs a detail call at all."""
    raw = load("smartrecruiters")["content"][0]

    assert smartrecruiters.normalize(raw, COMPANY, SLUG).description is None


def test_smartrecruiters_description_appears_once_detail_is_merged() -> None:
    listing = load("smartrecruiters")["content"][0]
    detail = load("smartrecruiters_detail")

    job = smartrecruiters.normalize({**listing, **detail}, COMPANY, SLUG)

    assert job.description
    assert "<" not in job.description
    # The detail payload also supplies the real posting URL the list omits.
    assert job.url == detail["postingUrl"]


def test_smartrecruiters_url_falls_back_to_a_constructed_link() -> None:
    raw = load("smartrecruiters")["content"][0]

    job = smartrecruiters.normalize(raw, COMPANY, SLUG)

    assert job.url == f"https://jobs.smartrecruiters.com/{SLUG}/{raw['id']}"


@pytest.mark.parametrize(
    ("location", "expected"),
    [
        ({"remote": True, "hybrid": False}, RemoteMode.REMOTE),
        ({"remote": False, "hybrid": True}, RemoteMode.HYBRID),
        # Both false really does mean on-site here: SmartRecruiters models all three.
        ({"remote": False, "hybrid": False}, RemoteMode.ONSITE),
        ({}, None),
    ],
)
def test_smartrecruiters_remote_mapping(
    location: dict[str, Any], expected: RemoteMode | None
) -> None:
    raw = {**load("smartrecruiters")["content"][0], "location": location}

    assert smartrecruiters.normalize(raw, COMPANY, SLUG).remote_mode is expected


# -------------------------------------------------------------------------- recruitee


def test_recruitee_fetch_targets_the_company_subdomain() -> None:
    seen: list[httpx.Request] = []
    body = load("recruitee")

    postings = recruitee.fetch(mock_client(body, seen), SLUG)

    assert str(seen[0].url) == f"https://{SLUG}.recruitee.com/api/offers/"
    assert postings == body["offers"]


def test_recruitee_normalize_maps_careers_url_and_strips_html() -> None:
    raw = load("recruitee")["offers"][0]

    job = recruitee.normalize(raw, COMPANY, SLUG)

    assert job.title == raw["title"]
    assert job.url == raw["careers_url"]
    assert job.external_id == f"{SLUG}:{raw['id']}"
    assert job.description
    assert "<p>" not in job.description


@pytest.mark.parametrize(
    ("flags", "expected"),
    [
        ({"remote": False, "hybrid": True, "on_site": False}, RemoteMode.HYBRID),
        ({"remote": True, "hybrid": False, "on_site": False}, RemoteMode.REMOTE),
        ({"remote": False, "hybrid": False, "on_site": True}, RemoteMode.ONSITE),
        # Non-exclusive by design: remote AND on-site is hybrid in substance.
        ({"remote": True, "hybrid": False, "on_site": True}, RemoteMode.HYBRID),
        ({"remote": False, "hybrid": False, "on_site": False}, None),
    ],
)
def test_recruitee_three_non_exclusive_booleans(
    flags: dict[str, bool], expected: RemoteMode | None
) -> None:
    """These three booleans are why `remote_mode` is not a boolean column."""
    raw = {**load("recruitee")["offers"][0], **flags}

    assert recruitee.normalize(raw, COMPANY, SLUG).remote_mode is expected


# ------------------------------------------------------------------- cross-adapter rules


@pytest.mark.parametrize(
    ("ats", "postings"),
    [
        (AtsType.GREENHOUSE, load("greenhouse")["jobs"]),
        (AtsType.LEVER, load("lever")),
        (AtsType.ASHBY, load("ashby")["jobs"]),
        (AtsType.WORKABLE, load("workable")["jobs"]),
        (AtsType.SMARTRECRUITERS, load("smartrecruiters")["content"]),
        (AtsType.RECRUITEE, load("recruitee")["offers"]),
    ],
)
def test_every_adapter_produces_a_slug_scoped_id_and_required_fields(
    ats: AtsType, postings: list[dict[str, Any]]
) -> None:
    """The invariant that keeps `uq_jobs_source_external_id` a dedupe key rather than a
    cross-tenant overwrite: every id is namespaced by the board it came from."""
    module = ADAPTERS[ats]

    for raw in postings:
        job = module.normalize(raw, COMPANY, SLUG)
        assert job.external_id.startswith(f"{SLUG}:")
        assert job.external_id != f"{SLUG}:None"
        assert job.source == ats.value
        assert job.ats_type is ats
        assert job.title
        assert job.url.startswith("http")
        assert job.company
        # Nothing dropped on the way through: raw_json is the whole payload, which is
        # where every field no column has yet (applyUrl, salary, department) survives.
        assert job.raw_json == raw
        if job.posted_at is not None:
            assert job.posted_at.tzinfo is not None
        if job.description is not None:
            assert "<p>" not in job.description
