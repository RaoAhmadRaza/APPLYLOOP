"""Working Nomads.

    GET https://www.workingnomads.com/api/exposed_jobs/

A bare JSON list, whole listing in one response. **No identifier of any kind** — the
payload is url, title, description, company_name, category_name, tags, location,
pub_date and nothing else. The URL is therefore the key; see `_native_id` for why it is
stripped down first.

`tags` is a comma-separated string here, not a list. It stays in `raw_json` untouched.
"""

from typing import Any
from urllib.parse import urlparse

import httpx
from schemas.enums import RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

SOURCE = "workingnomads"
HOME = "https://www.workingnomads.com"
URL = "https://www.workingnomads.com/api/exposed_jobs/"
COMPLETE = True
INTERVAL_HOURS = 12
PAGES = 1


def fetch(client: httpx.Client, pages: int = 1, delay: float = 0.0) -> list[dict[str, Any]]:
    response = client.get(URL)
    response.raise_for_status()
    payload: list[dict[str, Any]] = response.json() or []
    return [entry for entry in payload if isinstance(entry, dict) and entry.get("url")]


def _native_id(url: str) -> str:
    """Host and path only — no scheme, no query, no trailing slash.

    The identifier has to be stable across passes or every posting reposts as a new row
    and the whole feed churns. A tracking parameter, an http/https flip or an added
    slash would each do that, and all three are things a site changes without noticing.
    """
    parsed = urlparse(url)
    return f"{parsed.netloc}{parsed.path}".lower().rstrip("/")


def normalize(raw: dict[str, Any]) -> JobCreate:
    location = raw.get("location") or None
    return JobCreate(
        source=SOURCE,
        external_id=f"{SOURCE}:{_native_id(raw['url'])}",
        title=raw["title"],
        company=str(raw["company_name"]).strip(),
        location=location,
        locations=[location] if location else [],
        remote_mode=RemoteMode.REMOTE,
        description=html_to_text(raw.get("description")),
        url=raw["url"],
        ats_type=None,
        posted_at=to_utc(raw.get("pub_date")),
        raw_json=raw,
    )
