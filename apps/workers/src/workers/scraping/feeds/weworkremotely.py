"""We Work Remotely.

    GET https://weworkremotely.com/remote-jobs.rss

The one feed that is XML rather than JSON. Parsed with stdlib `xml.etree`, not
`defusedxml`: the documented risk is entity-expansion on untrusted input, and this is a
known first-party host over TLS whose response we cap at MAX_BYTES before parsing. A new
dependency for one 100-item feed is not proportionate; if a second XML source ever
appears, revisit.

Richer than the docs suggest — items carry `region`, `country`, `state`, `skills`,
`category` and `type` alongside the usual RSS fields. The employer is embedded in the
title as "Company: Title", which is the one place this adapter has to guess, and it
guesses conservatively: no colon means the whole string is the title.

`fetch` returns dicts, not Elements, so `raw_json` stays a JSON object and every other
feed's contract holds.
"""

import xml.etree.ElementTree as ElementTree
from email.utils import parsedate_to_datetime
from typing import Any

import httpx
from schemas.enums import RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import join_nonempty
from workers.scraping.text import html_to_text

SOURCE = "weworkremotely"
HOME = "https://weworkremotely.com"
URL = "https://weworkremotely.com/remote-jobs.rss"
COMPLETE = True
INTERVAL_HOURS = 12
PAGES = 1

# The live feed is under 1 MB. This is the guard that lets stdlib xml.etree be the right
# answer here rather than a dependency: an expansion attack needs a body to expand, and
# a body this size cannot carry one worth worrying about.
MAX_BYTES = 8 * 1024 * 1024

# Namespaced children (media:content) are noise for our purposes; keeping them would put
# "{http://search.yahoo.com/mrss}content" in raw_json as a key.
_NAMESPACED = "{"


def fetch(client: httpx.Client, pages: int = 1, delay: float = 0.0) -> list[dict[str, Any]]:
    response = client.get(URL, headers={"Accept": "application/rss+xml"})
    response.raise_for_status()
    if len(response.content) > MAX_BYTES:
        raise ValueError(f"{SOURCE}: response is {len(response.content)} bytes, over MAX_BYTES")

    channel = ElementTree.fromstring(response.text).find("channel")
    if channel is None:
        return []
    return [_as_dict(item) for item in channel.findall("item")]


def _as_dict(item: ElementTree.Element) -> dict[str, Any]:
    return {
        child.tag: (child.text or "").strip()
        for child in item
        if not child.tag.startswith(_NAMESPACED)
    }


def _company_and_title(raw_title: str) -> tuple[str, str]:
    """ "Logicstics: International Moving Sales" -> ("Logicstics", "International...").

    No colon means we do not know the employer, and inventing one is worse than an
    honest duplicate — the title becomes both, and dedupe simply will not merge it.
    """
    company, separator, title = raw_title.partition(":")
    if not separator or not title.strip():
        return raw_title.strip(), raw_title.strip()
    return company.strip(), title.strip()


def normalize(raw: dict[str, Any]) -> JobCreate:
    company, title = _company_and_title(str(raw["title"]))
    # `region` is the remote scope ("Anywhere in the World"); state/country are the
    # employer's own, and only matter when region is missing.
    location = raw.get("region") or join_nonempty([raw.get("state"), raw.get("country")])
    return JobCreate(
        source=SOURCE,
        external_id=f"{SOURCE}:{raw['guid']}",
        title=title,
        company=company,
        location=location,
        locations=[location] if location else [],
        remote_mode=RemoteMode.REMOTE,
        description=html_to_text(raw.get("description")),
        url=raw.get("link") or raw["guid"],
        ats_type=None,
        # RFC 2822 ("Thu, 06 Aug 2026 19:33:01 +0000"), which fromisoformat cannot read.
        posted_at=parsedate_to_datetime(raw["pubDate"]) if raw.get("pubDate") else None,
        raw_json=raw,
    )
