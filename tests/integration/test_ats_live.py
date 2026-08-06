"""M1 gate item 1, against the real boards.

    APPLYLOOP_LIVE_ATS=1 uv run pytest tests/integration/test_ats_live.py

Skipped by default, and deliberately not in CI: a build must not go red because a third
party had a bad afternoon. The recorded fixtures in tests/unit/test_ats_adapters.py are
what run on every commit.

What this catches that fixtures cannot: upstream shape drift. §3.7 names the failure mode
— "a scraper that returns zero rows because a board changed its markup raises no
exception". Run it when touching an adapter, and periodically to check the fixtures have
not gone stale.

Every board below was returning postings on 2026-08-06. If one starts failing, check
whether the company moved ATS before assuming the adapter broke — that is the registry
doing its job (§4.3's "auto-retire slugs that 404 for N runs"), not a bug here.
"""

import os

import pytest
from schemas.enums import AtsType
from workers.scraping import ADAPTERS, http

pytestmark = pytest.mark.skipif(
    not os.getenv("APPLYLOOP_LIVE_ATS"),
    reason="hits real third-party ATS endpoints; set APPLYLOOP_LIVE_ATS=1 to run",
)

# One real board per provider, with the company name the registry would supply.
LIVE_BOARDS = [
    (AtsType.GREENHOUSE, "vercel", "Vercel"),
    (AtsType.LEVER, "gopuff", "Gopuff"),
    (AtsType.ASHBY, "linear", "Linear"),
    (AtsType.WORKABLE, "blueground", "Blueground"),
    (AtsType.SMARTRECRUITERS, "Visa", "Visa"),
    (AtsType.RECRUITEE, "channable", "Channable"),
]


@pytest.mark.parametrize(("ats", "slug", "company"), LIVE_BOARDS)
def test_adapter_reads_its_real_board(ats: AtsType, slug: str, company: str) -> None:
    adapter = ADAPTERS[ats]

    with http.client() as client:
        postings = adapter.fetch(client, slug)

    # Zero rows is the silent failure this whole test exists for.
    assert postings, f"{ats.value}:{slug} returned no postings"

    for raw in postings:
        job = adapter.normalize(raw, company, slug)
        assert job.external_id.startswith(f"{slug}:")
        assert job.title
        assert job.url.startswith("http")
        assert job.source == ats.value
        if job.description is not None:
            assert "<p>" not in job.description


def test_smartrecruiters_detail_still_carries_the_description() -> None:
    """The one provider whose descriptions need a second request. If this shape moves,
    every SmartRecruiters job silently lands with description NULL."""
    adapter = ADAPTERS[AtsType.SMARTRECRUITERS]

    with http.client() as client:
        listing = adapter.fetch(client, "Visa")[0]
        detail = adapter.fetch_detail(client, "Visa", listing["id"])

    assert detail is not None
    job = adapter.normalize({**listing, **detail}, "Visa", "Visa")
    assert job.description
    assert job.url.startswith("https://jobs.smartrecruiters.com/")


def test_greenhouse_list_still_includes_first_published() -> None:
    """The docs say this field is detail-endpoint-only; the live list has it, and the
    adapter relies on that to avoid one detail call per job."""
    adapter = ADAPTERS[AtsType.GREENHOUSE]

    with http.client() as client:
        postings = adapter.fetch(client, "vercel")

    assert any(raw.get("first_published") for raw in postings)


def test_detect_resolves_a_real_board_url() -> None:
    """Gate item 3, end to end."""
    from workers.scraping import detect

    with http.client() as client:
        assert detect.detect(client, "https://jobs.ashbyhq.com/linear") == (
            AtsType.ASHBY,
            "linear",
        )
