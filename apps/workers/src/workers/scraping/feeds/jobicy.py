"""Jobicy.

    GET https://jobicy.com/api/v2/remote-jobs?count=100

`count` caps at 100 and there is no offset parameter, so one pass is the newest hundred
and nothing more — hence `COMPLETE = False`. The salary block (`salaryMin`, `salaryMax`,
`salaryCurrency`, `salaryPeriod`) is the most structured of the eight feeds, which M4
will want; it is optional and absent on most postings, so it stays in `raw_json` rather
than becoming a column nothing reads yet.

Their notice requires crediting Jobicy with a direct link and pointing every apply
button at the original job URL.
"""

from typing import Any

import httpx
from schemas.enums import RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

SOURCE = "jobicy"
HOME = "https://jobicy.com"
URL = "https://jobicy.com/api/v2/remote-jobs"
COMPLETE = False
INTERVAL_HOURS = 6
PAGES = 1

# Their documented ceiling.
COUNT = 100


def fetch(client: httpx.Client, pages: int = 1, delay: float = 0.0) -> list[dict[str, Any]]:
    response = client.get(URL, params={"count": COUNT})
    response.raise_for_status()
    jobs: list[dict[str, Any]] = response.json().get("jobs") or []
    return jobs


def normalize(raw: dict[str, Any]) -> JobCreate:
    location = raw.get("jobGeo") or None
    return JobCreate(
        source=SOURCE,
        external_id=f"{SOURCE}:{raw['id']}",
        title=raw["jobTitle"],
        company=str(raw["companyName"]).strip(),
        location=location,
        locations=[location] if location else [],
        remote_mode=RemoteMode.REMOTE,
        description=html_to_text(raw.get("jobDescription")) or html_to_text(raw.get("jobExcerpt")),
        url=raw["url"],
        ats_type=None,
        posted_at=to_utc(raw.get("pubDate")),
        raw_json=raw,
    )
