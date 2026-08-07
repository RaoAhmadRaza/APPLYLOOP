"""Layer 3 against the real endpoints.

    APPLYLOOP_LIVE_FEEDS=1 uv run pytest tests/integration/test_feeds_live.py

A separate variable from `APPLYLOOP_LIVE_ATS`, deliberately. Layer 1 costs nothing and
can be run freely while touching an adapter; layer 3 spends a metered budget — Remotive's
own response asks for at most four requests a day — and you have to be able to run one
without spending the other.

Skipped by default and not in CI, for the same reason test_ats_live.py is: a build must
not go red because a third party had a bad afternoon.

What this catches that fixtures cannot is upstream shape drift, which §3.7 names as the
most common real failure — "a scraper that returns zero rows because a board changed its
markup raises no exception". Each assertion below pins one shape an adapter would break
silently on, not the whole payload.

Every feed was returning postings on 2026-08-07.
"""

import os
from types import ModuleType

import pytest
from workers.scraping import http
from workers.scraping.feeds import FEEDS, himalayas, remoteok, weworkremotely, workingnomads

pytestmark = pytest.mark.skipif(
    not os.getenv("APPLYLOOP_LIVE_FEEDS"),
    reason="hits real third-party feeds on a metered budget; set APPLYLOOP_LIVE_FEEDS=1",
)


@pytest.mark.parametrize("source", sorted(FEEDS), ids=sorted(FEEDS))
def test_feed_still_returns_postings_we_can_normalize(source: str) -> None:
    """One page each — enough to prove the shape without spending the budget."""
    module: ModuleType = FEEDS[source]

    with http.client() as client:
        postings = module.fetch(client, 1, 0.0)

    # Zero rows is the silent failure this whole file exists for.
    assert postings, f"{source} returned no postings"

    ids = set()
    for raw in postings:
        job = module.normalize(raw)
        assert job.external_id.startswith(f"{source}:")
        assert job.title
        assert job.company
        assert job.url.startswith("http")
        assert job.source == source
        assert job.ats_type is None
        if job.description is not None:
            assert "<p>" not in job.description
        ids.add(job.external_id)

    # A collision here means the upsert would silently keep one and drop the other.
    assert len(ids) == len(postings), f"{source} produced duplicate external_ids"


def test_remoteok_still_answers_our_honest_user_agent() -> None:
    """Two live facts the adapter depends on.

    The endpoint is widely reported to 403 anything without "Mozilla" in the User-Agent,
    and it does refuse some clients — but not ours. As long as that holds we send the
    same honest string as every other request. If this starts failing, add a per-request
    header in remoteok.fetch; do not touch http.py.

    The second half: element 0 is a legal notice, and the adapter filters on `id` rather
    than skipping index 0. If the notice ever gains an id it would become a job with no
    title."""
    with http.client() as client:
        response = client.get(remoteok.URL)
        postings = remoteok.fetch(client, 1, 0.0)

    assert response.status_code == 200
    assert all(raw.get("id") for raw in postings)
    assert any("legal" in raw for raw in response.json())


def test_himalayas_still_returns_a_placeholder_company_name() -> None:
    """We derive the employer from `companySlug` because `companyName` is the literal
    string "name" on every posting. The day they fix it, this fails and the adapter's
    fallback quietly starts using the real field."""
    with http.client() as client:
        postings = himalayas.fetch(client, 1, 0.0)

    assert all(raw.get("companySlug") for raw in postings)
    assert any(raw.get("companyName") == "name" for raw in postings)


def test_workingnomads_still_ships_no_identifier() -> None:
    """Its external_id is derived from the URL precisely because there is nothing else.
    If they add an id, switch to it — a real key beats a derived one."""
    with http.client() as client:
        postings = workingnomads.fetch(client, 1, 0.0)

    assert all(raw.get("url") for raw in postings)
    assert not any({"id", "guid", "slug"} & set(raw) for raw in postings)


def test_weworkremotely_still_embeds_the_company_in_the_title() -> None:
    """The one place a feed adapter has to guess. If the colon convention goes, every
    WWR row starts filing under the wrong employer."""
    with http.client() as client:
        postings = weworkremotely.fetch(client, 1, 0.0)

    assert postings
    assert sum(":" in raw["title"] for raw in postings) > len(postings) / 2
