"""Every layer-3 feed, against payloads recorded from its real endpoint.

The fixtures in tests/fixtures/feeds/ are verbatim recordings (trimmed to a few
postings) taken on 2026-08-07. Each one deliberately keeps the trap it exists to prove:
Remotive's envelope keys, RemoteOK's legal-notice element, Himalayas' placeholder
`companyName`, Arbeitnow's epoch-seconds `created_at`, Working Nomads' complete absence
of an id, and WeWorkRemotely's "Company: Title".

Same machinery as test_ats_adapters.py: httpx.MockTransport, no respx, no vcr.
Assertions name the *source* field on each side, so they pin the mapping rather than
restating it.
"""

import json
from pathlib import Path
from types import ModuleType
from typing import Any

import httpx
import pytest
from schemas.enums import RemoteMode
from workers.scraping.feeds import (
    FEEDS,
    arbeitnow,
    himalayas,
    jobicy,
    remoteok,
    remotive,
    themuse,
    weworkremotely,
    workingnomads,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "feeds"


def load(name: str) -> Any:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def mock_client(
    payload: Any, seen: list[httpx.Request] | None = None, status: int = 200
) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(status, json=payload)

    return httpx.Client(transport=httpx.MockTransport(handler))


def xml_client(text: str, seen: list[httpx.Request] | None = None) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(request)
        return httpx.Response(200, text=text, headers={"content-type": "application/rss+xml"})

    return httpx.Client(transport=httpx.MockTransport(handler))


# Every feed's postings, unwrapped the way its own `fetch` unwraps them.
POSTINGS: dict[str, list[dict[str, Any]]] = {
    "remotive": load("remotive")["jobs"],
    "remoteok": [entry for entry in load("remoteok") if entry.get("id")],
    "himalayas": load("himalayas")["jobs"],
    "arbeitnow": load("arbeitnow")["data"],
    "jobicy": load("jobicy")["jobs"],
    "workingnomads": load("workingnomads"),
    "themuse": load("themuse")["results"],
}


# --------------------------------------------------------------------------- registry


def test_registry_covers_every_layer_three_feed() -> None:
    """§4.1 names eight. A module added without a registry entry silently never runs,
    and one registered without a beat entry silently never runs either — the second half
    is asserted in tests/integration/test_scraping_tasks.py."""
    assert set(FEEDS) == {
        "remotive",
        "remoteok",
        "himalayas",
        "arbeitnow",
        "weworkremotely",
        "jobicy",
        "workingnomads",
        "themuse",
    }


def test_every_feed_declares_an_attribution_target() -> None:
    """Remotive, RemoteOK, Himalayas and Jobicy all make a link back a licence
    condition. Keeping it as a constant means compliance is a property of the code
    rather than a promise in a README nobody reads."""
    for source, module in FEEDS.items():
        assert module.HOME.startswith("https://"), source


def test_a_paginated_feed_is_never_marked_complete() -> None:
    """COMPLETE decides whether absence may close a row, so getting it wrong retires a
    whole feed's history. A feed we page through cannot be complete by definition: a
    posting missing from the pages we asked for may be on a page we did not.

    The converse is not an invariant. Jobicy is one request and still incomplete — its
    `count` caps at 100 with no offset, so a single pass is a truncation, not the
    listing.
    """
    for source, module in FEEDS.items():
        if module.PAGES > 1:
            assert not module.COMPLETE, source


# --------------------------------------------------------------------------- remotive


def test_remotive_unwraps_jobs_and_ignores_the_envelope() -> None:
    seen: list[httpx.Request] = []

    postings = remotive.fetch(mock_client(load("remotive"), seen))

    assert str(seen[0].url) == remotive.URL
    assert len(postings) == len(load("remotive")["jobs"])
    # The envelope carries 00-warning / 0-legal-notice / job-count. None of them is a job.
    assert all("company_name" in raw for raw in postings)


def test_remotive_strips_whitespace_from_the_company_name() -> None:
    """Live payloads carry "Creative Force " and "Creative Force" for one employer. Left
    alone, dedupe would read them as two."""
    raw = {**POSTINGS["remotive"][0], "company_name": "  Creative Force  "}

    assert remotive.normalize(raw).company == "Creative Force"


def test_remotive_maps_candidate_required_location() -> None:
    raw = POSTINGS["remotive"][0]

    job = remotive.normalize(raw)

    assert job.location == raw["candidate_required_location"]
    assert job.locations == [raw["candidate_required_location"]]


# --------------------------------------------------------------------------- remoteok


def test_remoteok_drops_every_element_without_an_id() -> None:
    """Element 0 is a legal notice, not a posting. Filtering on `id` rather than
    skipping index 0 means a second notice cannot become a job with no title."""
    payload = load("remoteok")

    postings = remoteok.fetch(mock_client(payload))

    assert len(postings) == len(payload) - 1
    assert all("legal" not in raw for raw in postings)


def test_remoteok_does_not_override_the_user_agent() -> None:
    """Third-party write-ups say this endpoint needs a "Mozilla" User-Agent. Verified
    live on 2026-08-07, it does not — it answers our own honest string. Sending a
    browser-ish UA we do not need would be pretending to be something we are not.
    test_feeds_live.py is what would catch this changing."""
    seen: list[httpx.Request] = []

    remoteok.fetch(mock_client(load("remoteok"), seen))

    assert "User-Agent" not in seen[0].headers or "Mozilla" not in seen[0].headers["User-Agent"]


def test_remoteok_cleans_a_half_filled_location() -> None:
    """Live payloads read "Queensland, " — the region half of a join never filled in."""
    raw = {**POSTINGS["remoteok"][0], "location": "Queensland, "}

    assert remoteok.normalize(raw).location == "Queensland"


def test_remoteok_reads_position_as_the_title() -> None:
    raw = POSTINGS["remoteok"][0]

    assert remoteok.normalize(raw).title == raw["position"]


# -------------------------------------------------------------------------- himalayas


def test_himalayas_derives_the_company_from_the_slug() -> None:
    """Their API returns the literal string "name" in every companyName. companySlug is
    the only usable employer identifier."""
    raw = POSTINGS["himalayas"][0]
    assert raw["companyName"] == "name", "fixture no longer carries the placeholder"

    assert himalayas.normalize(raw).company == "Absencesoft Llc"


def test_himalayas_prefers_a_real_company_name_if_they_ever_fix_it() -> None:
    raw = {**POSTINGS["himalayas"][0], "companyName": "AbsenceSoft"}

    assert himalayas.normalize(raw).company == "AbsenceSoft"


def test_himalayas_pages_by_offset_at_their_documented_ceiling() -> None:
    seen: list[httpx.Request] = []

    himalayas.fetch(mock_client(load("himalayas"), seen), pages=3)

    assert [request.url.params["offset"] for request in seen] == ["0", "20", "40"]
    assert all(request.url.params["limit"] == "20" for request in seen)


def test_himalayas_stops_paging_on_429_instead_of_raising() -> None:
    """Raising hands it to Celery's autoretry, which re-runs the whole pass against the
    host that just asked us to slow down."""
    postings = himalayas.fetch(mock_client({}, status=429), pages=5)

    assert postings == []


def test_himalayas_reads_pubdate_as_epoch_seconds() -> None:
    """1786075366 is 2026. Read as milliseconds it would be 1970 — a wrong date, and
    not an error."""
    job = himalayas.normalize(POSTINGS["himalayas"][0])

    assert job.posted_at is not None
    assert job.posted_at.year == 2026


# -------------------------------------------------------------------------- arbeitnow


def test_arbeitnow_pages_one_based() -> None:
    seen: list[httpx.Request] = []

    arbeitnow.fetch(mock_client(load("arbeitnow"), seen), pages=2)

    assert [request.url.params["page"] for request in seen] == ["1", "2"]


def test_arbeitnow_stops_on_an_empty_page() -> None:
    seen: list[httpx.Request] = []

    postings = arbeitnow.fetch(mock_client({"data": []}, seen), pages=5)

    assert postings == []
    assert len(seen) == 1


def test_arbeitnow_reads_created_at_as_epoch_seconds() -> None:
    job = arbeitnow.normalize(POSTINGS["arbeitnow"][0])

    assert job.posted_at is not None
    assert job.posted_at.year == 2026


@pytest.mark.parametrize(
    ("remote", "expected"),
    [
        (True, RemoteMode.REMOTE),
        # False cannot distinguish onsite from hybrid, so it says nothing — same
        # reasoning workable.py uses for its single boolean.
        (False, None),
    ],
)
def test_arbeitnow_maps_its_single_remote_boolean(
    remote: bool, expected: RemoteMode | None
) -> None:
    raw = {**POSTINGS["arbeitnow"][0], "remote": remote}

    assert arbeitnow.normalize(raw).remote_mode is expected


# ----------------------------------------------------------------------------- jobicy


def test_jobicy_asks_for_its_documented_maximum() -> None:
    seen: list[httpx.Request] = []

    jobicy.fetch(mock_client(load("jobicy"), seen))

    assert seen[0].url.params["count"] == str(jobicy.COUNT)


def test_jobicy_reads_its_own_field_names() -> None:
    raw = POSTINGS["jobicy"][0]

    job = jobicy.normalize(raw)

    assert job.title == raw["jobTitle"]
    assert job.company == raw["companyName"]
    assert job.location == raw["jobGeo"]


# ---------------------------------------------------------------------- workingnomads


def test_workingnomads_external_id_is_stable_across_tracking_params() -> None:
    """The load-bearing one. This feed ships no identifier at all, so the url is the
    key — and a key that moves reposts the entire feed as new rows on the next pass."""
    raw = POSTINGS["workingnomads"][0]
    base = workingnomads.normalize(raw).external_id

    for variant in (
        raw["url"] + "?utm_source=applyloop",
        raw["url"].replace("https://", "http://"),
        raw["url"].rstrip("/") + "/",
        raw["url"].upper(),
    ):
        assert workingnomads.normalize({**raw, "url": variant}).external_id == base


def test_workingnomads_leaves_its_comma_string_tags_alone() -> None:
    """`tags` is a string here, not a list. raw_json holds exactly what came back."""
    raw = POSTINGS["workingnomads"][0]

    assert isinstance(raw["tags"], str)
    assert workingnomads.normalize(raw).raw_json["tags"] == raw["tags"]


# ---------------------------------------------------------------------------- themuse


def test_themuse_reads_its_own_dialect() -> None:
    raw = POSTINGS["themuse"][0]

    job = themuse.normalize(raw)

    assert job.title == raw["name"]
    assert job.company == raw["company"]["name"]
    assert job.url == raw["refs"]["landing_page"]
    assert job.locations == [entry["name"] for entry in raw["locations"]]


def test_themuse_says_nothing_about_remote() -> None:
    """The only feed of the eight that is not remote-only, and its payload carries no
    remote signal. Sniffing "Flexible / Remote" out of a location string is a guess."""
    assert themuse.normalize(POSTINGS["themuse"][0]).remote_mode is None


# --------------------------------------------------------------------- weworkremotely


def wwr_xml() -> str:
    return (FIXTURES / "weworkremotely.xml").read_text()


def wwr_items() -> list[dict[str, Any]]:
    return weworkremotely.fetch(xml_client(wwr_xml()))


def test_weworkremotely_returns_plain_dicts_not_elements() -> None:
    """raw_json is JSONB. An ElementTree node would not survive the round trip, and
    every other feed's contract says the payload is a JSON object."""
    items = wwr_items()

    assert items
    assert all(isinstance(item, dict) for item in items)
    assert all(isinstance(value, str) for item in items for value in item.values())


def test_weworkremotely_drops_namespaced_children() -> None:
    """media:content would otherwise become a raw_json key of
    "{http://search.yahoo.com/mrss}content"."""
    assert all(not key.startswith("{") for item in wwr_items() for key in item)


def test_weworkremotely_splits_the_employer_out_of_the_title() -> None:
    raw = {**wwr_items()[0], "title": "Logicstics: International Moving Sales"}

    job = weworkremotely.normalize(raw)

    assert job.company == "Logicstics"
    assert job.title == "International Moving Sales"


def test_weworkremotely_does_not_invent_an_employer_when_there_is_no_colon() -> None:
    """An honest duplicate beats a fabricated company name: dedupe simply will not
    merge it, which is the safe direction."""
    raw = {**wwr_items()[0], "title": "International Moving Sales"}

    job = weworkremotely.normalize(raw)

    assert job.company == "International Moving Sales"
    assert job.title == "International Moving Sales"


def test_weworkremotely_parses_rfc_2822_dates() -> None:
    """ "Thu, 06 Aug 2026 19:33:01 +0000", which fromisoformat cannot read."""
    job = weworkremotely.normalize(wwr_items()[0])

    assert job.posted_at is not None
    assert job.posted_at.tzinfo is not None
    assert job.posted_at.year == 2026


def test_weworkremotely_refuses_an_oversized_body() -> None:
    """The guard that lets stdlib xml.etree be the right answer here instead of a new
    dependency: an expansion attack needs a body to expand."""
    oversized = "<rss><channel>" + "<!-- pad -->" * 800_000 + "</channel></rss>"
    assert len(oversized.encode()) > weworkremotely.MAX_BYTES

    with pytest.raises(ValueError, match="over MAX_BYTES"):
        weworkremotely.fetch(xml_client(oversized))


# ---------------------------------------------------------------- cross-feed rules


ALL_POSTINGS = [
    pytest.param(FEEDS[source], postings, id=source) for source, postings in POSTINGS.items()
] + [pytest.param(weworkremotely, wwr_items(), id="weworkremotely")]


@pytest.mark.parametrize(("module", "postings"), ALL_POSTINGS)
def test_every_feed_produces_a_namespaced_id_and_required_fields(
    module: ModuleType, postings: list[dict[str, Any]]
) -> None:
    for raw in postings:
        job = module.normalize(raw)
        assert job.external_id.startswith(f"{module.SOURCE}:")
        assert job.external_id != f"{module.SOURCE}:None"
        assert job.source == module.SOURCE
        # A feed row must never claim to be first-party. One that did could win a
        # dedupe against the real ATS row and cost the survivor its apply URL (§4.2).
        assert job.ats_type is None
        assert job.title
        assert job.company
        assert job.url.startswith("http")
        # raw_json is the element the endpoint returned — not the envelope, not
        # enriched, not reordered (§6.3).
        assert job.raw_json == raw
        if job.posted_at is not None:
            assert job.posted_at.tzinfo is not None
        if job.description is not None:
            assert "<p>" not in job.description


@pytest.mark.parametrize(("module", "postings"), ALL_POSTINGS)
def test_every_feed_produces_unique_ids_within_one_pass(
    module: ModuleType, postings: list[dict[str, Any]]
) -> None:
    """The §6.3 collision test. Two postings sharing an external_id means the upsert
    silently keeps one and drops the other — and it is exactly the failure a feed with
    no native id would produce."""
    ids = [module.normalize(raw).external_id for raw in postings]

    assert len(set(ids)) == len(ids)
