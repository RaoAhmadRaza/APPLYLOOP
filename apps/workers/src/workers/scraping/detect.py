"""Which ATS does this company use, under which slug? (§4.3, gate item 3.)

No ATS publishes a list of its customers, so this mapping is infrastructure we have to
build ourselves — which is exactly why §4.3 calls the registry the moat.

Three tiers, cheapest first, stopping at the first hit:

1. **Parse the URL.** Zero network. Works when someone hands us a board link directly.
2. **Read the page.** The case that actually matters for a *real* careers URL: most
   companies serve their board from their own domain via an iframe or script tag, so
   `https://www.vanta.com/careers` only resolves once you look at the HTML.
3. **Probe.** §4.3's pseudocode — slugify the name and ask each provider in turn.

Tier 3 is last because it is the only one that can be wrong: a slug guess that happens
to match a *different* company's board would put their jobs under our row. `_looks_like`
guards against the weaker version of that (an endpoint answering 200 with something that
is not a job board) but cannot tell two real boards apart, so the probe requires the
board to be non-empty and the caller is expected to treat tier-3 hits as provisional.
"""

import re
from typing import Any

import httpx
from schemas.enums import AtsType

# Ordered: whichever pattern matches first wins, and the more specific host comes first.
# Greenhouse serves boards from three hosts (the legacy one, the current one, and the EU
# region); Lever has a matching EU host. Missing any of them silently drops that board.
_URL_PATTERNS: list[tuple[AtsType, re.Pattern[str]]] = [
    # The embed form must come first and be matched exactly: the slug lives in a query
    # parameter, and the generic pattern below would otherwise capture "embed".
    # Both /embed/job_board?for=X and /embed/job_board/js?for=X are in the wild.
    (
        AtsType.GREENHOUSE,
        re.compile(
            r"https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/embed/job_board"
            r"(?:/js)?\?for=([A-Za-z0-9_-]+)"
        ),
    ),
    (
        AtsType.GREENHOUSE,
        re.compile(
            r"https?://(?:job-boards|boards)(?:\.eu)?\.greenhouse\.io/(?!embed/)([A-Za-z0-9_-]+)"
        ),
    ),
    (AtsType.LEVER, re.compile(r"https?://jobs(?:\.eu)?\.lever\.co/([A-Za-z0-9_-]+)")),
    (AtsType.ASHBY, re.compile(r"https?://jobs\.ashbyhq\.com/([A-Za-z0-9_.-]+)")),
    (AtsType.WORKABLE, re.compile(r"https?://apply\.workable\.com/([A-Za-z0-9_-]+)")),
    (
        AtsType.SMARTRECRUITERS,
        re.compile(r"https?://(?:jobs|careers)\.smartrecruiters\.com/([A-Za-z0-9_-]+)"),
    ),
    (AtsType.RECRUITEE, re.compile(r"https?://([A-Za-z0-9_-]+)\.recruitee\.com")),
]

# Path segments that are part of the board's own URL structure, never a company slug.
_NOT_SLUGS = frozenset({"j", "embed", "api", "www", "jobs", "apply", "search"})

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# The probe reads the first page of each candidate board; a board with no postings tells
# us nothing and might just be a name collision.
_MIN_POSTINGS_TO_TRUST_A_PROBE = 1


def detect(client: httpx.Client, url_or_name: str) -> tuple[AtsType, str] | None:
    """Resolve a careers URL or a company name to `(ats_type, slug)`, or None."""
    direct = from_url(url_or_name)
    if direct:
        return direct

    if url_or_name.startswith(("http://", "https://")):
        embedded = from_page(client, url_or_name)
        if embedded:
            return embedded
        return None

    return probe(client, slugify(url_or_name))


def from_url(url: str) -> tuple[AtsType, str] | None:
    """Tier 1 — the URL is already a board link."""
    for ats, pattern in _URL_PATTERNS:
        # finditer, not search: a page can mention a board host in a path that is not a
        # slug ("/j/{shortcode}") before the real board link, and one bad first hit must
        # not stop the scan.
        for match in pattern.finditer(url):
            if match.group(1).lower() not in _NOT_SLUGS:
                return ats, match.group(1)
    return None


def from_page(client: httpx.Client, url: str) -> tuple[AtsType, str] | None:
    """Tier 2 — fetch the careers page and look for a board embedded in its markup."""
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError:
        return None
    return from_url(response.text)


def probe(client: httpx.Client, slug: str) -> tuple[AtsType, str] | None:
    """Tier 3 — §4.3's algorithm: try every provider with this slug, first hit wins."""
    # Imported here rather than at module scope: `workers.scraping` imports every
    # adapter, and the adapters must not import back into a module they may need.
    from workers.scraping import ADAPTERS

    for ats, adapter in ADAPTERS.items():
        try:
            postings = adapter.fetch(client, slug)
        except (httpx.HTTPError, KeyError, TypeError, ValueError):
            continue
        if _looks_like_postings(postings):
            return ats, slug
    return None


def _looks_like_postings(postings: Any) -> bool:
    """§4.3's `looks_like_jobs`.

    A 200 is not proof: parked domains, SPA shells and error pages all return those, and
    a probe asks six endpoints a slug none of them may know. So the payload has to be a
    non-empty list of objects that carry a title under one of the names the six
    providers use. An empty board is rejected too — it proves nothing and could be a
    name collision with a different company.
    """
    if not isinstance(postings, list) or len(postings) < _MIN_POSTINGS_TO_TRUST_A_PROBE:
        return False
    first = postings[0]
    return isinstance(first, dict) and any(key in first for key in ("title", "text", "name"))


def slugify(name: str) -> str:
    """Company name to the slug a board most likely uses. A guess, and tier 3 knows it."""
    return _NON_ALNUM.sub("", name.lower())
