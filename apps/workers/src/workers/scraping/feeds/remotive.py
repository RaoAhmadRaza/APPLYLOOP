"""Remotive.

    GET https://remotive.com/api/remote-jobs

Whole listing in one response, no pagination, no auth. The response body itself carries
the terms: "there is absolutely no need to request Remotive Job data too frequently...
we advise max. 4 times a day", and asks for a link back. Eight hours is three passes,
which leaves headroom for the two Celery retries a bad afternoon can spend.

The envelope also carries `00-warning`, `0-legal-notice`, `job-count` and
`total-job-count`. Only `jobs` is read; `raw_json` holds the element, never the
envelope (§6.3).
"""

from typing import Any

import httpx
from schemas.enums import RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

SOURCE = "remotive"
HOME = "https://remotive.com"
URL = "https://remotive.com/api/remote-jobs"
COMPLETE = True
INTERVAL_HOURS = 8
PAGES = 1


def fetch(client: httpx.Client, pages: int = 1, delay: float = 0.0) -> list[dict[str, Any]]:
    response = client.get(URL)
    response.raise_for_status()
    jobs: list[dict[str, Any]] = response.json().get("jobs") or []
    return jobs


def normalize(raw: dict[str, Any]) -> JobCreate:
    # Live payloads carry trailing whitespace on company names ("Creative Force "),
    # which would otherwise make two spellings of one employer look like two employers.
    company = str(raw["company_name"]).strip()
    location = raw.get("candidate_required_location") or None
    return JobCreate(
        source=SOURCE,
        external_id=f"{SOURCE}:{raw['id']}",
        title=raw["title"],
        company=company,
        location=location,
        locations=[location] if location else [],
        # A remote-only board. This is a fact about the feed, not an inference from the
        # posting.
        remote_mode=RemoteMode.REMOTE,
        description=html_to_text(raw.get("description")),
        url=raw["url"],
        ats_type=None,
        posted_at=to_utc(raw.get("publication_date")),
        raw_json=raw,
    )
