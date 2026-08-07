"""Himalayas.

    GET https://himalayas.app/jobs/api?offset={n}&limit=20

`limit` caps at 20 — their docs say it was reduced "to improve performance" — so full
coverage would be thousands of requests against a `totalCount` in the high five figures.
Ten pages an hour is 200 fresh postings a pass, ordered newest first, which is what the
feed is actually for. Hence `COMPLETE = False`: a pass never sees the whole listing, so
absence proves nothing.

**Their `companyName` is the literal string "name" on every posting** (and `companyLogo`
is literally "thumbnail_url") — placeholder values that reached production. `companySlug`
is the only usable employer identifier, so the name is derived from it, with the real
field preferred if they ever fix it.

Attribution: their terms require linking back to the Himalayas URL and naming them as
the source.
"""

from typing import Any

import httpx
from schemas.enums import RemoteMode
from schemas.job import JobCreate

from workers.scraping.feeds.paging import paged
from workers.scraping.parse import from_epoch_seconds
from workers.scraping.text import html_to_text

SOURCE = "himalayas"
HOME = "https://himalayas.app"
URL = "https://himalayas.app/jobs/api"
COMPLETE = False
INTERVAL_HOURS = 12
PAGES = 10

# Their documented ceiling. Asking for more silently returns 20.
PAGE_SIZE = 20

# What their API returns instead of a company name.
_PLACEHOLDER_NAME = "name"


def fetch(client: httpx.Client, pages: int = 1, delay: float = 0.0) -> list[dict[str, Any]]:
    return paged(
        client,
        lambda page: f"{URL}?offset={page * PAGE_SIZE}&limit={PAGE_SIZE}",
        lambda payload: payload.get("jobs") or [],
        pages,
        delay,
    )


def _company(raw: dict[str, Any]) -> str:
    name = str(raw.get("companyName") or "").strip()
    if name and name != _PLACEHOLDER_NAME:
        return name
    # "absencesoft-llc" -> "Absencesoft Llc". Lossy on capitalisation, but it is a real
    # employer identity rather than a placeholder, and the dedupe normalizer strips the
    # legal suffix anyway.
    return str(raw["companySlug"]).replace("-", " ").title()


def normalize(raw: dict[str, Any]) -> JobCreate:
    locations = [str(entry) for entry in raw.get("locationRestrictions") or [] if entry]
    return JobCreate(
        source=SOURCE,
        # `guid` is a URL rather than an opaque id. It is still their global identifier,
        # and it is stable.
        external_id=f"{SOURCE}:{raw['guid']}",
        title=raw["title"],
        company=_company(raw),
        location=locations[0] if locations else None,
        locations=locations,
        remote_mode=RemoteMode.REMOTE,
        description=html_to_text(raw.get("description")) or html_to_text(raw.get("excerpt")),
        url=raw.get("applicationLink") or raw["guid"],
        ats_type=None,
        # Epoch seconds, not milliseconds. See parse.from_epoch_seconds.
        posted_at=from_epoch_seconds(raw.get("pubDate")),
        raw_json=raw,
    )
