"""SmartRecruiters posting API.

    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=100&offset=N
    GET https://api.smartrecruiters.com/v1/companies/{slug}/postings/{id}

The only one of the six that is both paginated *and* withholds descriptions from the
list response. Full prose lives on the detail endpoint under
`jobAd.sections.{companyDescription,jobDescription,qualifications,additionalInformation}`,
which also carries the real `postingUrl` and `applyUrl`.

`fetch_detail` is optional in the adapter protocol; ingest calls it only for postings the
upsert reports as newly inserted, so steady state costs a handful of extra requests per
run rather than one per job.
"""

from typing import Any

import httpx
from schemas.enums import AtsType, RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import join_nonempty, to_utc
from workers.scraping.text import html_to_text

SOURCE = AtsType.SMARTRECRUITERS.value
ATS = AtsType.SMARTRECRUITERS
BASE_URL = "https://api.smartrecruiters.com/v1/companies"

# The documented per-page maximum.
PAGE_SIZE = 100

# A board larger than this means either a genuinely enormous employer or a paging bug
# that would otherwise spin forever. Stop and let the volume alert (§8.2) notice.
# ponytail: fixed ceiling, revisit if a real board legitimately exceeds it.
MAX_POSTINGS = 10_000

_SECTION_ORDER = ("companyDescription", "jobDescription", "qualifications", "additionalInformation")


def fetch(client: httpx.Client, slug: str) -> list[dict[str, Any]]:
    postings: list[dict[str, Any]] = []
    offset = 0
    while True:
        response = client.get(
            f"{BASE_URL}/{slug}/postings", params={"limit": PAGE_SIZE, "offset": offset}
        )
        response.raise_for_status()
        body = response.json()
        page: list[dict[str, Any]] = body.get("content") or []
        postings.extend(page)
        offset += len(page)
        if not page or offset >= body.get("totalFound", 0) or offset >= MAX_POSTINGS:
            return postings


def fetch_detail(client: httpx.Client, slug: str, native_id: str) -> dict[str, Any] | None:
    """The description-bearing half. None when the posting vanished between calls."""
    response = client.get(f"{BASE_URL}/{slug}/postings/{native_id}")
    if response.status_code == httpx.codes.NOT_FOUND:
        return None
    response.raise_for_status()
    detail: dict[str, Any] = response.json()
    return detail


def native_id(raw: dict[str, Any]) -> str:
    return str(raw["id"])


def _description(raw: dict[str, Any]) -> str | None:
    """Present only once the detail payload has been merged in."""
    sections = ((raw.get("jobAd") or {}).get("sections")) or {}
    parts = [html_to_text((sections.get(key) or {}).get("text")) for key in _SECTION_ORDER]
    return "\n\n".join(p for p in parts if p) or None


def _remote_mode(location: dict[str, Any]) -> RemoteMode | None:
    # SmartRecruiters models all three states explicitly, so both flags false really
    # does mean on-site here — unlike Workable's single boolean.
    if location.get("remote"):
        return RemoteMode.REMOTE
    if location.get("hybrid"):
        return RemoteMode.HYBRID
    return RemoteMode.ONSITE if location else None


def normalize(raw: dict[str, Any], company: str, slug: str) -> JobCreate:
    location = raw.get("location") or {}
    display = location.get("fullLocation") or join_nonempty(
        [location.get("city"), location.get("region"), location.get("country")]
    )
    return JobCreate(
        source=SOURCE,
        external_id=f"{slug}:{raw['id']}",
        title=raw["name"],
        company=(raw.get("company") or {}).get("name") or company,
        location=display,
        locations=[display] if display else [],
        remote_mode=_remote_mode(location),
        description=_description(raw),
        # The list response omits postingUrl; the id-only form redirects to the same page.
        url=raw.get("postingUrl") or f"https://jobs.smartrecruiters.com/{slug}/{raw['id']}",
        ats_type=ATS,
        posted_at=to_utc(raw.get("releasedDate")),
        raw_json=raw,
    )
