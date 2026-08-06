"""Greenhouse job board API.

    GET https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true

Whole board in one response, no pagination, no auth. Two places the live API disagrees
with its own documentation, both verified against boards-api.greenhouse.io/.../stripe:

  * The docs promise `{"jobs": [...], "meta": {"total": N}}`. There is no `meta`.
  * The docs list `first_published` as detail-endpoint-only. It is in the list response,
    which is why this adapter needs no per-job detail call.
"""

from typing import Any

import httpx
from schemas.enums import AtsType
from schemas.job import JobCreate

from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

SOURCE = AtsType.GREENHOUSE.value
ATS = AtsType.GREENHOUSE
BASE_URL = "https://boards-api.greenhouse.io/v1/boards"


def fetch(client: httpx.Client, slug: str) -> list[dict[str, Any]]:
    response = client.get(f"{BASE_URL}/{slug}/jobs", params={"content": "true"})
    response.raise_for_status()
    jobs: list[dict[str, Any]] = response.json().get("jobs") or []
    return jobs


def normalize(raw: dict[str, Any], company: str, slug: str) -> JobCreate:
    location = (raw.get("location") or {}).get("name")
    return JobCreate(
        source=SOURCE,
        external_id=f"{slug}:{raw['id']}",
        title=raw["title"],
        company=raw.get("company_name") or company,
        location=location,
        locations=[location] if location else [],
        # Greenhouse's payload carries no remote signal at all — not a flag, not a
        # workplace type. NULL means "the source did not say", which is the truth.
        # Sniffing the location string for "remote" would be a guess.
        remote_mode=None,
        # `content` is HTML that has itself been entity-escaped.
        description=html_to_text(raw.get("content")),
        url=raw["absolute_url"],
        ats_type=ATS,
        posted_at=to_utc(raw.get("first_published")),
        raw_json=raw,
    )
