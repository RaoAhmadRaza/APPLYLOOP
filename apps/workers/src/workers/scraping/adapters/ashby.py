"""Ashby public posting API.

    GET https://api.ashbyhq.com/posting-api/job-board/{slug}?includeCompensation=true

One response, no pagination. The board name is the last path segment of the company's
jobs.ashbyhq.com URL and is case-sensitive.

No `updatedAt` — only `publishedAt`. `secondaryLocations[]` is why `jobs.locations` is
an array: this is the provider that most often lists a role in fifteen countries.
"""

from typing import Any

import httpx
from schemas.enums import AtsType, RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

SOURCE = AtsType.ASHBY.value
ATS = AtsType.ASHBY
BASE_URL = "https://api.ashbyhq.com/posting-api/job-board"

_WORKPLACE = {
    "remote": RemoteMode.REMOTE,
    "hybrid": RemoteMode.HYBRID,
    "onsite": RemoteMode.ONSITE,
}


def fetch(client: httpx.Client, slug: str) -> list[dict[str, Any]]:
    response = client.get(f"{BASE_URL}/{slug}", params={"includeCompensation": "true"})
    response.raise_for_status()
    jobs: list[dict[str, Any]] = response.json().get("jobs") or []
    # isListed=False is a posting Ashby is holding back from its own board. Ingesting it
    # would surface a role the employer chose not to publish.
    return [job for job in jobs if job.get("isListed", True)]


def normalize(raw: dict[str, Any], company: str, slug: str) -> JobCreate:
    location = raw.get("location")
    secondary = [s.get("location") for s in raw.get("secondaryLocations") or []]
    locations = [loc for loc in [location, *secondary] if loc]
    return JobCreate(
        source=SOURCE,
        external_id=f"{slug}:{raw['id']}",
        title=raw["title"],
        # Ashby's payload never names the employer.
        company=company,
        location=location,
        locations=locations,
        # workplaceType over the separate isRemote boolean: the boolean can't say hybrid.
        remote_mode=_WORKPLACE.get((raw.get("workplaceType") or "").lower()),
        description=raw.get("descriptionPlain") or html_to_text(raw.get("descriptionHtml")),
        url=raw["jobUrl"],
        ats_type=ATS,
        posted_at=to_utc(raw.get("publishedAt")),
        raw_json=raw,
    )
