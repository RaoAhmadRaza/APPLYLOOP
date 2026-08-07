"""The Muse.

    GET https://www.themuse.com/api/public/jobs?page={n}

20 results a page. No API key: keyless is 500 requests an hour and five pages every six
hours needs 20 a day, so registering one would buy 100x headroom we have no use for and
add a secret to manage. Add it the day the limit actually bites.

The only feed of the eight that is not remote-only, so `remote_mode` stays NULL — the
payload carries no remote flag, and sniffing "Flexible / Remote" out of a location
string would be a guess.

Naming is its own dialect: the title is `name`, the employer is an object under
`company`, and the apply URL is `refs.landing_page`.
"""

from typing import Any

import httpx
from schemas.job import JobCreate

from workers.scraping.feeds.paging import paged
from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

SOURCE = "themuse"
HOME = "https://www.themuse.com"
URL = "https://www.themuse.com/api/public/jobs"
COMPLETE = False
INTERVAL_HOURS = 6
PAGES = 5


def fetch(client: httpx.Client, pages: int = 1, delay: float = 0.0) -> list[dict[str, Any]]:
    return paged(
        client,
        # Their `page` is 1-based.
        lambda page: f"{URL}?page={page + 1}",
        lambda payload: payload.get("results") or [],
        pages,
        delay,
    )


def normalize(raw: dict[str, Any]) -> JobCreate:
    locations = [
        str(entry["name"]) for entry in raw.get("locations") or [] if isinstance(entry, dict)
    ]
    return JobCreate(
        source=SOURCE,
        external_id=f"{SOURCE}:{raw['id']}",
        title=raw["name"],
        company=str((raw.get("company") or {})["name"]).strip(),
        location=locations[0] if locations else None,
        locations=locations,
        # Not a remote-only board, and the payload says nothing either way.
        remote_mode=None,
        description=html_to_text(raw.get("contents")),
        url=raw["refs"]["landing_page"],
        ats_type=None,
        posted_at=to_utc(raw.get("publication_date")),
        raw_json=raw,
    )
