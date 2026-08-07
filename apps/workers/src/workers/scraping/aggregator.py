"""Layer 2: aggregator breadth via JobSpy (§4.1). The only layer permitted a proxy.

Reaches LinkedIn, Indeed and Google — enterprise employers and roles that never appear
on a layer-1 ATS board. §7.4 makes this layer 2 rather than the backbone for good
reasons: aggregators cap results per search, rate-limit hard, sit behind Cloudflare, and
carry stale and ghost listings. Layer 1 stays the source of truth; this fills the gaps.

Not in `adapters/` and not in `feeds/`. It satisfies neither protocol — no
`fetch(client, slug)`, no `AtsType`, no per-provider module — and forcing it into
`ADAPTERS` would break `ingest_company`'s enum lookup. Layer 2 is a sibling of layer 1,
not a member of it.

Things worth knowing before changing anything here:

* **The only layer permitted a proxy** (§7.4, Part 13 rule 12). The ATS layer and the
  free feeds cost nothing to hit directly, and residential bandwidth is §8.1's swing
  factor. The proxy is an argument to `scrape_jobs`, never an environment variable —
  `http.client()` sets `trust_env=False` precisely so an ambient one cannot leak into
  the other two layers.
* **No account, so no ban risk to a user.** We read public listing pages through a
  third-party library. We hold no aggregator credentials, log into nothing and submit
  nothing. §3.2's ban risk is about *accounts*, which is exactly why this layer may run
  server-side while the apply path may not.
* **Containment is a WHERE clause.** Every row is `source LIKE 'jobspy:%'` and every site
  is one entry in `SITES`. Dropping a site, or the whole layer after a takedown notice,
  is a list edit and a DELETE rather than a refactor (§11).
* **Pacing is politeness and self-preservation at once.** LinkedIn throttles around the
  tenth page per IP (§5.4). The caps below are not tuned for speed.
* **Volume, not errors** (§3.7, §8.2). A blocked scraper returns an empty frame and
  raises nothing, so every run records its counts and a zero is an incident.
* **`raw_json` is what JobSpy returned**, unmodified (§6.3). We never see LinkedIn's own
  bytes; JobSpy *is* our source here.
"""

import hashlib
import json
from typing import Any

from schemas.enums import RemoteMode
from schemas.job import JobCreate

from workers.scraping.parse import to_utc
from workers.scraping.text import html_to_text

# Search terms live here rather than in a table or the environment, for the same reason
# `registry.SEED` does: this is operational data with a known replacement date. M3 lands
# profiles and M4 lands matching, and the real answer is "search what our users actually
# want". Until then these are the roles the product targets, chosen to overlap layer 1's
# coverage so the dedupe pass has something to collapse.
SEARCHES: list[tuple[str, str]] = [
    ("software engineer", "United States"),
    ("backend engineer", "United States"),
    ("data engineer", "United States"),
    ("machine learning engineer", "United States"),
    ("platform engineer", "Remote"),
    ("python developer", "Remote"),
]

# Start narrow. Glassdoor and ZipRecruiter join once these three are proven, and each is
# one list entry — which is also how one gets dropped.
SITES = ["indeed", "linkedin", "google"]

# LinkedIn throttles around the tenth page per IP and a page is roughly 25 results, so
# this keeps every search to two pages, well under.
RESULTS_WANTED = 50

# Small and fresh beats deep and historical: the back catalogue is layer 1's job.
HOURS_OLD = 72

# Descriptions cost one extra request *per job* and are precisely what trips the rate
# limit. §3.5 says filter before you spend — M4 should fetch descriptions for the
# shortlist, not for every scraped row.
FETCH_DESCRIPTIONS = False

SOURCE_PREFIX = "jobspy:"

# Length of the URL digest used when a site returns no id. Deterministic across runs,
# which a generated uuid would not be.
_DIGEST_LENGTH = 16


def source_for(site: str) -> str:
    """`jobs.source` has no CHECK by design, so the prefix is free — and it buys §11's
    containment query: dropping this layer is `DELETE FROM jobs WHERE source LIKE
    'jobspy:%'`, not a refactor."""
    return f"{SOURCE_PREFIX}{site}"


def to_rows(frame: Any) -> list[dict[str, Any]]:
    """A JobSpy DataFrame as plain JSON-safe dicts.

    pandas emits NaN, NaT, Timestamp and numpy scalars, none of which are JSON
    serialisable and all of which would land in `jobs.raw_json` (JSONB). `to_json` maps
    them deterministically — NaN and NaT to null, Timestamp to ISO — as a pure function
    of the frame. Hand-rolling a sanitiser here would be a worse version of it.
    """
    rows: list[dict[str, Any]] = json.loads(
        frame.to_json(orient="records", date_format="iso", date_unit="s")
    )
    return rows


def normalize(row: dict[str, Any]) -> JobCreate | None:
    """One JobSpy row to a `JobCreate`, or None when it is not a usable posting.

    Returning None rather than raising: one malformed row out of fifty is a bad row, not
    a failed run, and dropping it is visible in the recorded counts.
    """
    site = row.get("site")
    title = row.get("title")
    company = (row.get("company") or "").strip()
    url = row.get("job_url")
    if not site or not title or not company or not url:
        return None

    native = row.get("id") or _digest(url)
    location = row.get("location") or None
    description = row.get("description") or None

    return JobCreate(
        source=source_for(site),
        # §6.3's shape. JobSpy already prefixes its ids per site ("li-", "in-"), but the
        # namespace is stated rather than assumed — Google and Bayt return none at all.
        external_id=f"{site}:{native}",
        title=title,
        company=company,
        # Resolved later by §4.3's reverse-index, which reads `job_url_direct` out of
        # raw_json. Attaching one here would mean matching employer names against a
        # `companies.name` that is not unique.
        company_id=None,
        location=location,
        # NOT location.split(","). "San Francisco, CA, US" is one place, and splitting it
        # would poison M4's `&&` GIN filter with "CA" and "US" as if they were places.
        locations=[location] if location else [],
        # Never ONSITE. `is_remote=False` from an aggregator means "not detected" —
        # JobSpy infers it from a description keyword sniff on several sites — and NULL
        # is what RemoteMode reserves for "the source did not say". Mapping it to ONSITE
        # would put wrong data in front of M4's free hard filter and silently drop remote
        # jobs for remote-only users.
        remote_mode=RemoteMode.REMOTE if row.get("is_remote") else None,
        description=html_to_text(description),
        # §7.4: always prefer the direct ATS URL. `job_url_direct` is the employer's own
        # posting where the site exposes it, and it is also the seam §4.3's reverse-index
        # reads to grow the registry. LinkedIn leaves it null, hence the fallback.
        url=row.get("job_url_direct") or url,
        # `AtsType.OTHER` means "an ATS we have no adapter for", not "not an ATS".
        ats_type=None,
        posted_at=to_utc(row.get("date_posted")),
        # ponytail: LinkedIn derives date_posted from a relative timestamp, so the same
        # posting's row can differ between runs and the upsert's diff guard will rewrite
        # it. Accepted — M2's DoD asks for idempotent, which the unique constraint
        # delivers regardless. Do NOT "fix" this by mutating raw_json; that breaks §6.3
        # for M1 too.
        raw_json=row,
    )


def _digest(url: str) -> str:
    """A stable id for sites that return none. Deterministic, unlike a uuid."""
    return hashlib.sha256(url.encode()).hexdigest()[:_DIGEST_LENGTH]


def scrape(site: str, term: str, location: str, proxies: list[str]) -> list[JobCreate]:
    """One aggregator search. Returns nothing at all without a proxy — see the task."""
    if not proxies:
        return []

    # Imported here, not at module scope. jobspy/util.py does a module-level
    # `import tls_client`, which dlopen()s an architecture-specific shared object. A
    # top-level import would make that failure kill the Celery worker at boot — Celery
    # imports every entry in TASK_MODULES eagerly — and take M1's ATS ingestion down
    # with it. Here it fails as one task while ingest_all keeps running (§11).
    from jobspy import scrape_jobs

    frame = scrape_jobs(
        site_name=[site],
        search_term=term,
        location=location,
        results_wanted=RESULTS_WANTED,
        hours_old=HOURS_OLD,
        linkedin_fetch_description=FETCH_DESCRIPTIONS,
        proxies=proxies,
        verbose=0,
    )
    if frame is None or not len(frame):
        return []

    jobs = (normalize(row) for row in to_rows(frame))
    return [job for job in jobs if job is not None]
