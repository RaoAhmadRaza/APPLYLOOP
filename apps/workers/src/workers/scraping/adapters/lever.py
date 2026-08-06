"""Lever postings API.

    GET https://api.lever.co/v0/postings/{slug}?mode=json

Returns a bare JSON array, not an envelope. `skip`/`limit` exist but the unparameterised
call returns the whole board, so no paging loop.

No `updatedAt` — only `createdAt`, in epoch milliseconds. That absence is why M1's change
detection diffs payloads instead of comparing timestamps.
"""

from typing import Any

import httpx
from schemas.enums import AtsType, RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

SOURCE = AtsType.LEVER.value
ATS = AtsType.LEVER
BASE_URL = "https://api.lever.co/v0/postings"

# Lever's documented vocabulary. "unspecified" deliberately has no entry: it maps to
# NULL, which is what "the source did not say" means.
_WORKPLACE = {
    "remote": RemoteMode.REMOTE,
    "hybrid": RemoteMode.HYBRID,
    "on-site": RemoteMode.ONSITE,
}


def fetch(client: httpx.Client, slug: str) -> list[dict[str, Any]]:
    response = client.get(f"{BASE_URL}/{slug}", params={"mode": "json"})
    response.raise_for_status()
    postings: list[dict[str, Any]] = response.json() or []
    return postings


def _description(raw: dict[str, Any]) -> str | None:
    """Lever splits one posting's prose across three fields plus a list of sections.

    Using `descriptionPlain` alone drops the qualifications and duties, which is most of
    what M4 will want to embed.
    """
    parts: list[str | None] = [raw.get("descriptionPlain")]
    for section in raw.get("lists") or []:
        parts.append(section.get("text"))
        parts.append(html_to_text(section.get("content")))
    parts.append(raw.get("additionalPlain"))
    return "\n\n".join(p for p in parts if p) or None


def normalize(raw: dict[str, Any], company: str, slug: str) -> JobCreate:
    categories = raw.get("categories") or {}
    location = categories.get("location")
    locations = categories.get("allLocations") or ([location] if location else [])
    return JobCreate(
        source=SOURCE,
        external_id=f"{slug}:{raw['id']}",
        title=raw["text"],
        # Lever's payload never names the employer. The registry row is the only source.
        company=company,
        location=location,
        locations=locations,
        remote_mode=_WORKPLACE.get(raw.get("workplaceType") or ""),
        description=_description(raw),
        # hostedUrl is the posting page. applyUrl is the form; it stays in raw_json for
        # M9's extension, which is the first thing that needs it.
        url=raw["hostedUrl"],
        ats_type=ATS,
        posted_at=to_utc(raw.get("createdAt")),
        raw_json=raw,
    )
