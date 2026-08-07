"""Arbeitnow.

    GET https://www.arbeitnow.com/api/job-board-api?page={n}

Mostly EU/DE inventory, which is what makes it additive rather than another copy of the
same remote-tech pool. 175 postings a page; three pages an hour is plenty for a board
their own `meta.info` says is "updated every hour".

`meta.terms` asks for a link back and says "please do not abuse". `COMPLETE = False`
because `links.last` is null — there is no way to know from a page whether it was the
last one.
"""

from typing import Any

import httpx
from schemas.enums import RemoteMode
from schemas.job import JobCreate

from workers.scraping.feeds.paging import paged
from workers.scraping.parse import from_epoch_seconds
from workers.scraping.text import html_to_text

SOURCE = "arbeitnow"
HOME = "https://www.arbeitnow.com"
URL = "https://www.arbeitnow.com/api/job-board-api"
COMPLETE = False
INTERVAL_HOURS = 6
PAGES = 3


def fetch(client: httpx.Client, pages: int = 1, delay: float = 0.0) -> list[dict[str, Any]]:
    return paged(
        client,
        lambda page: f"{URL}?page={page + 1}",
        lambda payload: payload.get("data") or [],
        pages,
        delay,
    )


def normalize(raw: dict[str, Any]) -> JobCreate:
    location = raw.get("location") or None
    return JobCreate(
        source=SOURCE,
        external_id=f"{SOURCE}:{raw['slug']}",
        title=raw["title"],
        company=str(raw["company_name"]).strip(),
        location=location,
        locations=[location] if location else [],
        # An explicit boolean, so it is worth reading — but false cannot distinguish
        # onsite from hybrid, which is the same reason workable.py leaves it NULL.
        remote_mode=RemoteMode.REMOTE if raw.get("remote") else None,
        description=html_to_text(raw.get("description")),
        url=raw["url"],
        ats_type=None,
        # Epoch seconds, not milliseconds. Passing this to to_utc would silently yield
        # 1970 — a wrong date, not an error.
        posted_at=from_epoch_seconds(raw.get("created_at")),
        raw_json=raw,
    )
