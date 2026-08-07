"""Layer 3 sourcing: free remote-tech JSON feeds (§4.1).

A parallel registry rather than a generalised `ADAPTERS`, deliberately. `ADAPTERS` is
keyed on `AtsType` and `detect.probe` iterates it calling `fetch(client, slug)` —
probing Remotive with a company slug is nonsense, and giving `AtsType` a member for
feeds would need a CHECK drop-and-re-add migration and make `jobs.ats_type` mean two
different things. Two dicts is a smaller diff than a filter at every use site.

Same module-per-provider shape as layer 1, different arguments: a feed is not a board.
It has no tenant, so no slug, and it names the employer in the payload, so `normalize`
needs nothing passed in.

Four constants carry what the ingest loop has to know, and they live on the module
because they are facts about the provider's terms rather than about our deployment:

    SOURCE          jobs.source, and the external_id namespace
    HOME            attribution target — a licence condition on most of these
    COMPLETE        does ONE pass return the feed's entire current listing?
    INTERVAL_HOURS  the politeness budget

`COMPLETE` is the one that can destroy data. M1 closes every posting a board stopped
returning, which is only safe because an ATS board returns the complete current set for
one employer. A paginated feed proves nothing by absence — a posting missing from the
pages we asked for may simply be on a page we did not ask for. See `feed.ingest_feed`.

Verified against live responses, 2026-08-07. Where a provider's docs disagree with its
endpoint, the endpoint wins:

  * Remotive's own response body asks for at most four requests a day.
  * RemoteOK 403s a request whose User-Agent lacks "Mozilla", and element 0 of its list
    is a legal notice rather than a posting.
  * Himalayas returns the literal string "name" in every `companyName`, so the only
    usable employer identifier is `companySlug`. Its `limit` caps at 20.
  * Arbeitnow does paginate, despite returning no `next` cursor in the docs — `links`
    and `meta` are both present, and `created_at` is epoch seconds.
  * Working Nomads ships no identifier of any kind. Its `tags` is a comma string.
  * WeWorkRemotely is RSS, and embeds the employer in the title as "Company: Title".
  * The Muse calls the title `name` and returns the apply URL under `refs.landing_page`.
"""

from types import ModuleType
from typing import Any, Protocol

import httpx
from schemas.job import JobCreate

from workers.scraping.feeds import (
    arbeitnow,
    himalayas,
    jobicy,
    remoteok,
    remotive,
    themuse,
    weworkremotely,
    workingnomads,
)


class Feed(Protocol):
    """The layer-3 counterpart to `Adapter`. Same reasoning, different arguments."""

    SOURCE: str
    HOME: str
    COMPLETE: bool
    INTERVAL_HOURS: int
    PAGES: int

    def fetch(
        self, client: httpx.Client, pages: int = 1, delay: float = 0.0
    ) -> list[dict[str, Any]]: ...

    def normalize(self, raw: dict[str, Any]) -> JobCreate: ...


FEEDS: dict[str, ModuleType] = {
    remotive.SOURCE: remotive,
    remoteok.SOURCE: remoteok,
    himalayas.SOURCE: himalayas,
    arbeitnow.SOURCE: arbeitnow,
    weworkremotely.SOURCE: weworkremotely,
    jobicy.SOURCE: jobicy,
    workingnomads.SOURCE: workingnomads,
    themuse.SOURCE: themuse,
}
