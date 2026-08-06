"""M1 gate item 3: `detect()` returns the right `ats:slug` for a real careers URL.

Tier 1 (URL patterns) is exhaustively table-driven against every host each provider
actually serves boards from. Tiers 2 and 3 run through httpx.MockTransport.
"""

from typing import Any

import httpx
import pytest
from schemas.enums import AtsType
from workers.scraping import detect

# Every host shape seen in the wild. A missing row here is a board we silently never
# resolve — which shows up as an empty registry, not as an error.
URLS = [
    ("https://boards.greenhouse.io/stripe", AtsType.GREENHOUSE, "stripe"),
    ("https://job-boards.greenhouse.io/vercel", AtsType.GREENHOUSE, "vercel"),
    ("https://boards.eu.greenhouse.io/acme", AtsType.GREENHOUSE, "acme"),
    ("https://boards.greenhouse.io/stripe/jobs/8023928", AtsType.GREENHOUSE, "stripe"),
    ("https://boards.greenhouse.io/embed/job_board?for=onbe", AtsType.GREENHOUSE, "onbe"),
    ("https://jobs.lever.co/leverdemo", AtsType.LEVER, "leverdemo"),
    ("https://jobs.eu.lever.co/acme", AtsType.LEVER, "acme"),
    ("https://jobs.lever.co/leverdemo/33538a2f-d27d/apply", AtsType.LEVER, "leverdemo"),
    ("https://jobs.ashbyhq.com/Ashby", AtsType.ASHBY, "Ashby"),
    ("https://jobs.ashbyhq.com/Ashby/7458d4e9/application", AtsType.ASHBY, "Ashby"),
    ("https://apply.workable.com/blueground", AtsType.WORKABLE, "blueground"),
    ("https://jobs.smartrecruiters.com/Visa", AtsType.SMARTRECRUITERS, "Visa"),
    ("https://careers.smartrecruiters.com/Visa", AtsType.SMARTRECRUITERS, "Visa"),
    ("https://channable.recruitee.com", AtsType.RECRUITEE, "channable"),
    ("https://channable.recruitee.com/o/engineer", AtsType.RECRUITEE, "channable"),
]


def _client(handler: Any) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


# ------------------------------------------------------------------ tier 1: URL patterns


@pytest.mark.parametrize(("url", "ats", "slug"), URLS)
def test_board_urls_resolve_without_a_network_call(url: str, ats: AtsType, slug: str) -> None:
    assert detect.from_url(url) == (ats, slug)


def test_ashby_slugs_keep_their_case() -> None:
    """Ashby board names are case-sensitive; lowercasing one 404s the endpoint."""
    assert detect.from_url("https://jobs.ashbyhq.com/OpenAI") == (AtsType.ASHBY, "OpenAI")


def test_workable_short_links_are_not_mistaken_for_a_slug() -> None:
    """apply.workable.com/j/{shortcode} is one posting, not a board."""
    assert detect.from_url("https://apply.workable.com/j/186545F8C1") is None


def test_a_plain_careers_url_matches_nothing_on_its_own() -> None:
    assert detect.from_url("https://www.vanta.com/careers") is None
    assert detect.from_url("https://example.com") is None


# ------------------------------------------------------------------ tier 2: embedded board


def test_a_real_careers_page_resolves_from_its_embedded_board() -> None:
    """The case that matters: the company serves the board from its own domain."""
    # Arrange
    page = """
        <html><body><h1>Careers</h1>
        <script src="https://boards.greenhouse.io/embed/job_board/js?for=vanta"></script>
        <div id="grnhse_app"></div>
        </body></html>
    """
    client = _client(lambda _r: httpx.Response(200, text=page))

    # Act / Assert
    assert detect.detect(client, "https://www.vanta.com/careers") == (
        AtsType.GREENHOUSE,
        "vanta",
    )


def test_an_iframed_lever_board_resolves() -> None:
    page = '<iframe src="https://jobs.lever.co/acme?mode=iframe"></iframe>'
    client = _client(lambda _r: httpx.Response(200, text=page))

    assert detect.detect(client, "https://acme.com/jobs") == (AtsType.LEVER, "acme")


def test_a_careers_page_with_no_board_returns_none() -> None:
    client = _client(lambda _r: httpx.Response(200, text="<p>Email us.</p>"))

    assert detect.detect(client, "https://acme.com/careers") is None


def test_an_unreachable_page_returns_none_rather_than_raising() -> None:
    """detect() runs unattended over thousands of URLs; one dead host is not an outage."""

    def boom(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host")

    assert detect.detect(_client(boom), "https://gone.example/careers") is None


def test_a_404_careers_page_returns_none() -> None:
    client = _client(lambda _r: httpx.Response(404, text="nope"))

    assert detect.detect(client, "https://acme.com/careers") is None


# ------------------------------------------------------------------------- tier 3: probe


def test_a_company_name_probes_every_provider_until_one_answers() -> None:
    """§4.3's algorithm. Greenhouse is asked first and 404s; Lever answers."""
    asked: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        asked.append(request.url.host)
        if request.url.host == "api.lever.co":
            return httpx.Response(200, json=[{"text": "Engineer", "id": "1"}])
        return httpx.Response(404, json={})

    assert detect.detect(_client(handler), "Acme Corp") == (AtsType.LEVER, "acmecorp")
    assert "boards-api.greenhouse.io" in asked


def test_probe_rejects_an_endpoint_that_answers_200_with_something_else() -> None:
    """A 200 is not proof — parked domains and error pages return those too."""
    client = _client(lambda _r: httpx.Response(200, json={"jobs": [{"nonsense": True}]}))

    assert detect.probe(client, "acme") is None


def test_probe_rejects_an_empty_board() -> None:
    """An empty board proves nothing and may be a name collision with another company."""
    client = _client(lambda _r: httpx.Response(200, json={"jobs": []}))

    assert detect.probe(client, "acme") is None


def test_a_url_never_falls_through_to_probing() -> None:
    """Guessing a slug from a hostname would attach another company's jobs to this row."""
    calls: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(200, text="<p>nothing here</p>")

    assert detect.detect(_client(handler), "https://acme.com/careers") is None
    # Exactly one request: the page fetch. No six-provider probe afterwards.
    assert len(calls) == 1


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("Acme Corp", "acmecorp"),
        ("37signals", "37signals"),
        ("Doist!", "doist"),
        ("  Vanta  ", "vanta"),
    ],
)
def test_slugify(name: str, expected: str) -> None:
    assert detect.slugify(name) == expected
