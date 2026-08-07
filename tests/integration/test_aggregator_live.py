"""Layer 2 against the real aggregators, through the real proxy.

    APPLYLOOP_LIVE_AGGREGATOR=1 uv run pytest tests/integration/test_aggregator_live.py

Written now and skipped until residential proxy credentials exist, because the M2 gate
clause "JobSpy goes through the proxy" is only provable offline up to a point: the unit
tests prove `scrape_jobs` receives the list and that `http.client()` ignores every proxy
environment variable, but only this file proves rows actually arrive through it.

Never in CI. It costs metered residential bandwidth per run, and a build must not go red
because LinkedIn had a bad afternoon.

Requires JOBSPY_PROXIES to be set. Without it the aggregator no-ops by design, so the
skip below is a real precondition rather than a convenience.
"""

import os

import pytest
from workers.scraping import aggregator
from workers.settings import get_settings

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("APPLYLOOP_LIVE_AGGREGATOR"),
        reason="spends metered residential bandwidth; set APPLYLOOP_LIVE_AGGREGATOR=1",
    ),
    pytest.mark.skipif(
        not os.getenv("JOBSPY_PROXIES"),
        reason="the aggregator layer no-ops without a proxy, by design (§7.4)",
    ),
]


def _proxies() -> list[str]:
    return [proxy.get_secret_value() for proxy in get_settings().jobspy_proxies]


@pytest.mark.parametrize("site", aggregator.SITES)
def test_a_real_search_returns_normalizable_rows(site: str) -> None:
    """Zero rows is the silent failure §3.7 names: a blocked scraper returns an empty
    frame and raises nothing."""
    term, location = aggregator.SEARCHES[0]

    jobs = aggregator.scrape(site, term, location, _proxies())

    assert jobs, f"{site} returned no rows — blocked, or the search is too narrow"
    for job in jobs:
        assert job.source == f"jobspy:{site}"
        assert job.external_id.startswith(f"{site}:")
        assert job.title
        assert job.company
        assert job.url.startswith("http")
        assert job.ats_type is None


def test_the_ids_are_unique_within_one_search() -> None:
    """A collision means the upsert silently keeps one row and drops the other."""
    term, location = aggregator.SEARCHES[0]

    jobs = aggregator.scrape("indeed", term, location, _proxies())

    assert len({job.external_id for job in jobs}) == len(jobs)


def test_the_layer_still_no_ops_without_a_proxy() -> None:
    """The interlock, asserted where a real proxy exists to make it meaningful."""
    term, location = aggregator.SEARCHES[0]

    assert aggregator.scrape("indeed", term, location, []) == []
