# Decision log

Decisions made **while building**, with the evidence that drove them. Append-only.

CLAUDE.md Part 7 holds the decisions made *before* building — the platform and stack
choices. This file holds the ones discovered with a keyboard: the times a vendor's
documentation was wrong, a rule had to be narrowed because live data broke it, or a
plan-stage assumption did not survive contact.

**Format:** `Decision — rejected alternative — why`, plus the evidence where there is
any. An entry is worth adding when someone six months from now would otherwise
reasonably do the opposite.

**Do not add:** anything the code already says plainly, anything git history answers, or
a restatement of Part 7.

---

## M0 — Foundation (2026-08-06)

**One `postgresql+psycopg://` URL for both engines** — separate sync and async URLs —
psycopg3 is sync *and* async off the same string, so the API's async engine and Celery's
and Alembic's sync engines share one setting. Choosing asyncpg would have forced two.

**`sqlalchemy[asyncio]` is a hard requirement, not an extra** — a bare `sqlalchemy` —
SQLAlchemy's greenlet marker lists `aarch64` but not `arm64`, so installing without the
extra on an Apple Silicon Mac silently omits greenlet and every async ORM call raises
`MissingGreenlet`. Presents as "works in Docker, broken locally", which wastes an
afternoon.

**`NullPool` on the sync engine** — a pooled engine plus a `worker_process_init` reset
hook — Celery's prefork children inherit pooled sockets and corrupt each other's protocol
state. A connection per task makes the failure structurally impossible instead of patched.

**Settings read through a cached `get_settings()`** — a module-level `Settings()` — an
import-time read makes the module unimportable without a complete environment, which
breaks tests and tooling. The entrypoints call it at module scope, so a misconfigured
container still dies at boot.

**Bounded strings are `TEXT` + a named `CHECK` built from a `StrEnum`** — a native
Postgres `ENUM` — a native enum cannot `ADD VALUE` inside the transaction Alembic wraps
migrations in, and removing a value rewrites the table. Adding a value here is a
drop-and-re-add of one constraint. Guarded by `tests/unit/test_enums_match_checks.py`.

**Volume mounts at `/var/lib/postgresql`** — the historical `/var/lib/postgresql/data` —
Postgres 18 moved `PGDATA` to a version-scoped path. Mounting the old one means the
volume is silently ignored and every `compose down` loses the database. Do not "fix" it.

---

## M1 — ATS ingestion and the slug registry (2026-08-06)

**Change detection is a payload diff, never a timestamp comparison** — comparing
`updated_at` — verified against all six live providers: **only Greenhouse exposes an
update timestamp.** The other five expose creation or publication dates only. Postgres
answers the diff for free because `jsonb` equality is semantic and key-order-insensitive,
which is why no `content_hash` column exists.

**`jobs.external_id` is always `f"{slug}:{native_id}"`** — the bare native id —
Greenhouse integers and Lever/Ashby UUIDs are provably unique across tenants; Recruitee's
integer `id` and Workable's `shortcode` are not. Without the prefix a collision makes
`uq_jobs_source_external_id` overwrite one employer's posting with another's — the
constraint causing corruption instead of preventing it.

**`raw_json` holds exactly what the list endpoint returned** — enriching it with detail
payloads or computed fields — it is the value the next run diffs against, so any
enrichment makes every run find a difference and rewrite every row. Hit and fixed during
M1 with the SmartRecruiters detail merge.

**`RETURNING xmax = 0` to split inserts from updates** — a second query, or a
`content_hash` — one round trip, and rows the `WHERE` filters out are not returned at all,
so the result set *is* the changed set.

**`closed_at` is a timestamp, not a status enum or a boolean** — `is_open BOOLEAN` — it
records *when* a posting vanished, which a boolean throws away, and it needs no CHECK
constraint to drop and re-add when the vocabulary changes.

**`RemoteMode` is a three-value enum, not a boolean** — `remote BOOLEAN` — Recruitee
returns three *non-exclusive* booleans (`remote`, `hybrid`, `on_site`), so a boolean
silently flattens hybrid roles. NULL means "the source did not say", which Greenhouse
always does.

**Celery uses an explicit `include=TASK_MODULES`** — `autodiscover_tasks(["workers.tasks"])`
— autodiscover appends `related_name` to each entry, so it looked for `workers.tasks.tasks`,
matched nothing, and registered nothing while `celery inspect ping` still passed. The test
suite hid it by importing task modules directly, which registers them as a side effect.
**A unit test cannot catch this** — importing the module to check it is what masks the bug
— so CI asks the running worker via `inspect registered`.

**Beat schedules M1, not n8n** — standing up n8n early — §7.1 names n8n but that is M7's
milestone. The beat entry holds no business logic, so M7 swaps in cron→webhook without
touching ingest.

**Adapters are modules, not a class hierarchy** — an `Adapter` ABC — same reasoning §5.2
gives for refusing an `ApplyStrategy` ABC. The `Protocol` costs nothing at runtime and
still documents one shape.

---

## M2 — Aggregators, feeds, dedupe, registry growth (2026-08-07)

### Defects found in shipped code

**`http.client()` passes `trust_env=False`** — leaving httpx's default — `httpx.Client`
defaults to `trust_env=True` and reads `HTTP_PROXY`/`HTTPS_PROXY`/`ALL_PROXY`. Nothing
set one before M2, so the defect was invisible — but M2 is the milestone that puts a
proxy in the worker's environment, and on that day every ATS and feed request would have
started going through metered bandwidth. **The existing adapter tests structurally could
not catch it:** httpx skips env-proxy resolution when a transport is supplied, so
`MockTransport` always sees empty mounts.

**`upsert` collapses a batch on `external_id` before writing** — trusting sources not to
repeat — Postgres refuses an `ON CONFLICT DO UPDATE` that would touch a row twice in one
statement. Verified live: **a Workable board repeats a posting once per location** —
`lawnstarter` returns 46 entries for 9 shortcodes. None of M1's three seeded Workable
boards repeat, which is why this only surfaced once M2's reverse-index found one that
does. Offset-paginated feeds do the same when a posting is inserted between two pages.

**`upsert` chunks at 500 rows** — one multi-VALUES statement — Postgres caps a statement
at 65,535 bind parameters and `JobCreate` has 13 fields, so a single statement died just
above 5,041 rows. `smartrecruiters.MAX_POSTINGS` is already 10,000.

**Beat's schedule lives on a named volume** — `/tmp` inside the container — beat stores
last-run times there, so every restart looked like "never ran" and re-fired every entry
immediately. Harmless at M1's unmetered six-hour tick; a real violation once Remotive's
four-a-day budget is on that clock. The directory must be created **in the image** owned
by `app`, or Docker seeds the volume root-owned and beat cannot write.

### Layer 3 — the free feeds

**A parallel `FEEDS` registry** — generalising `ADAPTERS` — that dict is keyed on
`AtsType` and `detect.probe` iterates it calling `fetch(client, slug)`; probing Remotive
with a company slug is nonsense. Giving `AtsType` a member for feeds would need a CHECK
drop-and-re-add migration and make `jobs.ats_type` mean two different things.

**`COMPLETE` governs whether absence may close a row** — closing whatever a pass did not
return — M1's rule is only safe because an ATS board returns the complete current set for
one employer. A paginated or truncated feed proves nothing by absence. Getting this wrong
retires a whole feed's history in one tick.

**The volume floor is a gate on the close, not a dashboard metric** — recording the drop
and closing anyway — by the time a human reads a dashboard the rows are already closed.

**Each feed's interval lives on its module, not in settings** — eight pairs of env vars —
it is a property of that provider's terms (Remotive's own response asks for at most four
requests a day) and does not change between staging and production.

**WeWorkRemotely uses stdlib `xml.etree` behind an 8 MB cap** — adding `defusedxml` — the
documented risk is entity expansion on untrusted input; this is a known first-party host
over TLS whose body we bound before parsing. A dependency for one 100-item feed is not
proportionate. Revisit if a second XML source appears.

**The Muse runs keyless** — registering for an API key — 500 requests/hour against a need
of 20/day is 100x headroom, and a key is a secret to manage. Add it when the limit bites.

**RemoteOK gets no User-Agent override** — sending a `Mozilla/…` string — every
third-party write-up says the endpoint 403s without one, and it does refuse some clients.
**Verified live 2026-08-07: it answers our own honest `applyloop/0.1` string.** Sending a
browser-ish UA we do not need would be pretending to be something we are not.
`test_feeds_live.py` fails if this stops being true.

**Working Nomads' `external_id` is derived from its URL** — anything else — the payload
ships **no identifier of any kind**. Scheme and query are stripped and the trailing slash
removed, because a key that moves reposts the entire feed as new rows on the next pass.

**Himalayas' company comes from `companySlug`** — `companyName` — their API returns the
literal string `"name"` in every `companyName` and `"thumbnail_url"` in every
`companyLogo`. Placeholders that reached production. The real field is preferred if they
ever fix it.

### Cross-source dedupe

**`canonical_id IS NULL` means "this row is the survivor"** — a self-pointing id, or a
`job_duplicates` table — the polarity makes the downstream contract one indexable
predicate (`closed_at IS NULL AND canonical_id IS NULL`), and every row predating the
migration is already correctly marked with no data step.

**Losers are marked, never deleted** — deleting the duplicate — `upsert` conflicts on
`(source, external_id)`, so a deleted row has no conflict target and the source's next
pass re-inserts it. Delete/insert forever, and §6.3's "writes only diffs" gone.

**Priority is a pure function of `jobs.source`** — resolving ties by `created_at` or
arrival order — that is what makes "the survivor keeps the ATS apply URL" *structural*.
If an ATS row is open in the group it wins, and its `url` already **is** the ATS URL, so
nothing is copied and nothing can be copied wrong.

**Dedupe only ever collapses ACROSS sources** — collapsing any matching key — §4.2 is
about an aggregator row matching an ATS row, not about second-guessing a source's own
listing. **Evidence:** a live run over 933 real rows produced 61 merges, *all* of them
`lever → lever`, and every one was a distinct opening at a distinct Gopuff site.

**`#` is not a requisition-id delimiter** — treating `#NNN` as noise like `- NNN` —
**evidence:** live Gopuff data reads `Operations Associate, Bridgeport, #259`, where that
is a **store number**. Stripping it merged two postings at facilities in different states
(CT and PA), because `normalize_location` also collapses both to `bridgeport`. A dash or
a bracket is a requisition id; a hash is not reliably anything.

**`normalize_location` takes the first comma component only** — a state/country
abbreviation table — one rule collapses `San Francisco, CA` / `San Francisco, California,
United States` / `San Francisco` with no lookup table, and keeps multi-city openings of
one title apart. **Known ceiling:** `Portland, OR` and `Portland, ME` collide for one
employer posting one title in both.

**Seniority is never stripped, and there is no fuzzy matching** — trigram or embedding
similarity — a false merge silently removes a real job from the pool, which is worse than
a surviving duplicate: a duplicate costs one wasted match, a false merge costs an
application the user never got to make. A similarity threshold would also make the winner
non-deterministic between runs and break the idempotency the stage depends on.

**Dedupe gets its own beat entry** — a chord after `ingest_all` — the pass converges to a
fixed point from any starting state, so *when* it runs is not load-bearing, and a chord
would let one failing board block the dedupe of every other source.

### Registry growth (§4.3)

**Tier 1 searches the payloads we already stored, at zero request cost** — probing every
unknown employer — `detect.from_url` is already a `finditer` over arbitrary text, which is
exactly what `detect.from_page` does to a fetched HTML body. Pointing it at `raw_json`
resolves Himalayas' `applicationLink`, RemoteOK's `apply_url` and JobSpy's
`job_url_direct` for free, with no per-source code.

**A probe hit is verified against the board's own company name** — trusting the slug guess
— `detect.py`'s contract says tier-3 hits are provisional and puts verification on the
caller. Registering the wrong board writes another employer's postings into ours, which is
corruption of the thing §4.3 calls the moat. Boards that name no employer (Lever, Ashby)
are still accepted, or most real hits would be rejected.

**The negative cache is a real registry row (`ats_type='other'`, `status='error'`)** — a
new table or column — both values already existed, both CHECKs already accept them, and
"no ATS we could find" is a legitimate answer about a company. Needs no migration.
`ingest_all` gained an `ats_type != 'other'` clause because there is no adapter behind it.

**A cached miss deliberately does NOT link its rows** — linking them like a hit —
`company_id IS NULL` is what marks an employer as still worth resolving, so linking would
retire it permanently and make `grow_retry_days` unreachable. Companies do adopt an ATS
later. **Found by the retry test failing**, which is what it was written for.

### Layer 2 — JobSpy

**JobSpy is a worker dependency pinned to a git sha** — the PyPI release; a separate
sidecar service — the release cannot install here: **1.1.82 (2024-12-20) pins
`NUMPY==1.26.3`**, which publishes no cp313 wheel. Upstream relaxed it in Jan 2026 and has
cut no release since. Pinned by sha rather than branch because a scraper tracking a moving
branch is an unreviewed deploy on every rebuild.

`docs/job-automation-build-blueprint.md` sketches "JobSpy Dockerized + FastAPI wrapper",
but that line predates this workspace and its only justification is dependency isolation,
which the sha pin removes. What would remain is a second Dockerfile, a compose service, a
healthcheck, a manifest outside `uv.lock` and a network hop — to install one library.

**Verified before committing:** resolves on 3.13 (numpy 2.5.1, pandas 2.3.3) and imports
cleanly inside **both** `linux/arm64` and `linux/amd64` containers, so tls-client does ship
the aarch64 object and **no `platform:` pin is needed**.

**The `scrape_jobs` import lives inside the task** — module scope — `jobspy/util.py` does
a module-level `import tls_client` which `dlopen()`s an architecture-specific shared
object, and Celery imports every `TASK_MODULES` entry eagerly. A top-level import would
make that failure kill the worker at boot and take M1's ATS ingestion with it (§11).

**`is_remote=False` maps to NULL, never `ONSITE`** — the obvious mapping — it means "not
detected"; JobSpy infers it from a description keyword sniff on several sites. `ONSITE`
would put wrong data in front of M4's *free* hard filter and silently drop remote jobs for
remote-only users.

**No `JOBSPY_ALLOW_UNPROXIED` escape hatch** — a flag for local testing — config for the
one value that should never change, and the "just this once" that gets the IP banned. A
developer who wants it unproxied can do so in a shell; the *scheduled* path stays
interlocked.

**`close_missing` is never called on the aggregator** — reusing M1's close — an aggregator
search is a query, not a board. "This job did not come back in today's `python developer`
search" means it fell off page two, not that it closed. Closing on absence would
mass-close live jobs on the first throttled run.

---

## Open, deferred deliberately

| Item | Trigger to revisit | Recorded |
|---|---|---|
| **CLAUDE.md §8.1's proxy budget ("$50 → hundreds", "2–5x infra cost")** contradicts measurement — the shipped config moves 7.6 MiB/pass, ≈0.45 GB/month, ≈$1/month. See `docs/proxy-setup.md` §2. §8.1 is right for dozens of terms hourly with per-job descriptions. | **End of project / M11 hardening**, alongside the real cost dashboards. Deliberately not changed now: the estimate becomes true again the moment `SEARCHES` or the cadence grows. | 2026-08-07 |
| `google` returns **0 rows even unproxied from a residential IP** — a rotted JobSpy selector or a wrong search shape, not a blocking problem | Before relying on layer 2 for coverage. Debug or drop it from `aggregator.SITES` — it is one list entry. | 2026-08-07 |
| `jobs.apply_url` as an indexed column | M9's extension needs the lookup. Every ATS returns one and `raw_json` keeps it. | M1 |
| ETag / conditional GET on feeds | Bandwidth becomes visible. Needs an `etag` column. | M1 |
| A `scrape_runs` table, `jobs.last_seen_at` | `events` stops answering §8.2's per-run counts. | M2 |
| The Muse API key | The keyless 500 req/hr limit actually bites. | M2 |
| `defusedxml` | A second XML source appears, or WWR's feed stops being first-party. | M2 |
| Fuzzy / trigram dedupe | M4's golden set shows duplicate pairs surviving the exact key. | M2 |
| A city+region map for `normalize_location` | A real board produces the `Portland, OR` / `Portland, ME` collision. | M2 |
| `apps/jobspy` sidecar | Only if a future JobSpy bump breaks the in-worker install on both arches. | M2 |
| Portal-risk field on `jobs` | M9. `source` + `ats_type` already answer it at read time; a column with no consumer is speculative. | M2 |
