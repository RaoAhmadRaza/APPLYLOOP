"""Workable public widget API.

    GET https://apply.workable.com/api/v1/widget/accounts/{slug}?details=true

CLAUDE.md §4.1 names a different endpoint — `api/v3/accounts/{account}/jobs`, POST. That
one works, but needs a JSON body and `nextPage` token paging. This v1 widget GET returns
the same public postings *plus* descriptions in a single unpaginated call, so it is the
cheaper correct answer. v3 is the upgrade path if a board ever exceeds what v1 returns.

Trap verified live: **every job's `id` is `null`.** `shortcode` is the identifier.
"""

from typing import Any

import httpx
from schemas.enums import AtsType, RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import join_nonempty, to_utc
from workers.scraping.text import html_to_text

SOURCE = AtsType.WORKABLE.value
ATS = AtsType.WORKABLE
BASE_URL = "https://apply.workable.com/api/v1/widget/accounts"


def fetch(client: httpx.Client, slug: str) -> list[dict[str, Any]]:
    response = client.get(f"{BASE_URL}/{slug}", params={"details": "true"})
    response.raise_for_status()
    jobs: list[dict[str, Any]] = response.json().get("jobs") or []
    return jobs


def _remote_mode(raw: dict[str, Any]) -> RemoteMode | None:
    """`telecommuting` is a boolean, and a boolean cannot say hybrid.

    True is unambiguous. False is not — Workable's own v3 `workplace` field has three
    values, so v1's False collapses on-site and hybrid together. Returning ONSITE here
    would mislabel every hybrid role, which is the exact flattening migration 0003 was
    written to prevent. NULL is the honest answer.
    """
    return RemoteMode.REMOTE if raw.get("telecommuting") else None


def _description(raw: dict[str, Any]) -> str | None:
    parts = [
        html_to_text(raw.get("description")),
        html_to_text(raw.get("requirements")),
        html_to_text(raw.get("benefits")),
    ]
    return "\n\n".join(p for p in parts if p) or None


def normalize(raw: dict[str, Any], company: str, slug: str) -> JobCreate:
    location = join_nonempty([raw.get("city"), raw.get("state"), raw.get("country")])
    locations = [
        loc
        for loc in (
            join_nonempty([entry.get("city"), entry.get("region"), entry.get("country")])
            for entry in raw.get("locations") or []
        )
        if loc
    ]
    return JobCreate(
        source=SOURCE,
        # `id` is null on every posting; shortcode is the real key.
        external_id=f"{slug}:{raw['shortcode']}",
        title=raw["title"],
        company=company,
        location=location,
        locations=locations or ([location] if location else []),
        remote_mode=_remote_mode(raw),
        description=_description(raw),
        url=raw.get("url") or raw["shortlink"],
        ats_type=ATS,
        posted_at=to_utc(raw.get("published_on") or raw.get("created_at")),
        raw_json=raw,
    )
