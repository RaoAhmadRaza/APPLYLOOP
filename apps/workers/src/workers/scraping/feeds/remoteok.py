"""Remote OK.

    GET https://remoteok.com/api

A bare JSON list whose **element 0 is a legal notice**, not a posting. Elements are
filtered on having an `id` rather than by dropping index 0: the day they add a second
notice, an index-based skip would silently ingest it as a job with no title.

Their terms require a followed link back to the posting's canonical URL on remoteok.com,
which is what `url` holds.
"""

from typing import Any

import httpx
from schemas.enums import RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

SOURCE = "remoteok"
HOME = "https://remoteok.com"
URL = "https://remoteok.com/api"
COMPLETE = True
INTERVAL_HOURS = 12
PAGES = 1


def fetch(client: httpx.Client, pages: int = 1, delay: float = 0.0) -> list[dict[str, Any]]:
    # No User-Agent override. Third-party write-ups say this endpoint 403s anything
    # without "Mozilla" in the UA, and it does refuse some clients — but verified on
    # 2026-08-07 it answers 200 to the shared client's honest `applyloop/0.1` string.
    # A browser-ish UA we do not need would be pretending to be something we are not
    # for no reason. test_feeds_live.py asserts this stays true; if it starts failing,
    # the fix is a per-request header here, not a change to http.py.
    response = client.get(URL)
    response.raise_for_status()
    payload = response.json()
    return [entry for entry in payload if isinstance(entry, dict) and entry.get("id")]


def normalize(raw: dict[str, Any]) -> JobCreate:
    # Live payloads read "Queensland, " — the region half of a "city, region" join that
    # was never filled in.
    location = (raw.get("location") or "").strip().strip(",").strip() or None
    return JobCreate(
        source=SOURCE,
        external_id=f"{SOURCE}:{raw['id']}",
        # `position`, not `title`.
        title=raw["position"],
        company=str(raw["company"]).strip(),
        location=location,
        locations=[location] if location else [],
        remote_mode=RemoteMode.REMOTE,
        description=html_to_text(raw.get("description")),
        url=raw["url"],
        ats_type=None,
        # `date` is ISO; `epoch` beside it is seconds, and reading the ISO one avoids
        # having to know that.
        posted_at=to_utc(raw.get("date")),
        raw_json=raw,
    )
