"""Recruitee careers-site API.

    GET https://{slug}.recruitee.com/api/offers/

No auth, no pagination. `description` and `requirements` are raw HTML.

This is the provider that motivated `RemoteMode`: it returns three *non-exclusive*
booleans — `remote`, `hybrid`, `on_site` — so a single boolean column could not have
represented it.
"""

from typing import Any

import httpx
from schemas.enums import AtsType, RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

SOURCE = AtsType.RECRUITEE.value
ATS = AtsType.RECRUITEE


def fetch(client: httpx.Client, slug: str) -> list[dict[str, Any]]:
    response = client.get(f"https://{slug}.recruitee.com/api/offers/")
    response.raise_for_status()
    offers: list[dict[str, Any]] = response.json().get("offers") or []
    return offers


def _remote_mode(raw: dict[str, Any]) -> RemoteMode | None:
    # Hybrid is checked first, and remote+on_site together is hybrid in substance even
    # when the explicit flag is unset. The booleans are non-exclusive by design.
    if raw.get("hybrid") or (raw.get("remote") and raw.get("on_site")):
        return RemoteMode.HYBRID
    if raw.get("remote"):
        return RemoteMode.REMOTE
    if raw.get("on_site"):
        return RemoteMode.ONSITE
    return None


def normalize(raw: dict[str, Any], company: str, slug: str) -> JobCreate:
    locations = [
        loc
        for loc in (entry.get("name") or entry.get("city") for entry in raw.get("locations") or [])
        if loc
    ]
    location = raw.get("location") or (locations[0] if locations else None)
    description = "\n\n".join(
        part
        for part in (
            html_to_text(raw.get("description")),
            html_to_text(raw.get("requirements")),
        )
        if part
    )
    return JobCreate(
        source=SOURCE,
        # Recruitee's integer id is not provably unique across tenants; the slug prefix
        # makes it so. See the module docstring in workers/scraping/__init__.py.
        external_id=f"{slug}:{raw['id']}",
        title=raw["title"],
        company=raw.get("company_name") or company,
        location=location,
        locations=locations or ([location] if location else []),
        remote_mode=_remote_mode(raw),
        description=description or None,
        url=raw["careers_url"],
        ats_type=ATS,
        posted_at=to_utc(raw.get("published_at") or raw.get("created_at")),
        raw_json=raw,
    )
