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

## M3 — Profiles, résumé parsing and the evidence vault (2026-08-07)

### The vault, and where §3.3 is actually won

**The evidence vault is a table, verified at write time** — a JSONB key on `profiles`;
trusting the extractor — this is the decision the rest of M3 hangs off, so it is first.

§3.3 makes M5's validator the guardrail against fabrication: it diffs every generated
bullet against the vault and strips what it cannot trace. **That guarantee is worth
nothing if the vault itself is not true.** If the *parser* invents a skill, M5 finds it,
declares the generated bullet traceable, and puts a lie on somebody's résumé — the
validator working perfectly and proving nothing. So `vault.py` stores a claim only when
its text appears in that profile's `master_resume`.

A table rather than a JSONB key for a lifecycle reason rather than a normalisation one:
`parsed_json` is *derived* and is thrown away and rebuilt on every re-parse, while the
vault also holds `origin='user'` claims a person added by hand, which have to survive a
re-upload. Same row shape, two lifetimes, so two homes.

**Comparison is containment over letters and digits, not similarity** — a trigram or
embedding threshold — the same reasoning `dedupe.py` gives, with the polarity reversed.
There, a false merge loses a real job. Here, a *rejected true claim* silently loses real
evidence, and an *admitted paraphrase* is precisely the thing being excluded. Stricter
comparison fails on a line break markitdown introduced mid-bullet; looser waves through
"reduced latency by 76%" when nobody wrote 76%.

**Rejected claims are counted onto the `profile.parsed` event** — dropping them
silently — a non-zero count means the model paraphrased instead of copying, which loses
real evidence that nothing else would surface. §3.7's "alert on volume" applies to
evidence as much as to rows. `test_the_model_copies_rather_than_paraphrases` gates the
ratio at 15% in the live suite.

**Skill keywords each become their own claim** — storing the group label — a résumé
writes "Languages: Python, Go", and §3.3 names inventing a *skill* as the adversarial
case the permanent M5 test must catch. Left inside a group label, "is Rust in the
vault?" is unanswerable.

### The parser

**The LLM extracts facts; Python derives filters** — asking the model for seniority and
years of experience — `profiles.seniority`, `profiles.locations` and `profiles.work_auth`
are M4 `WHERE`-clause inputs, and a value that varies between two runs over one résumé
drops a different set of jobs each time. The model reports titles and dates; `derive.py`
does the judging, with tests and no network.

**Years of experience merges overlapping intervals** — summing them — a job and a
concurrent contract are one stretch of a life, and summing reports eight years as
twelve. A bare year ends at the *following* January, because "2020 – 2021" is two years
of work and resolving both ends to January reports it as one month. **Found by a test**:
the first implementation anchored on `spans[0]` before sorting, which mis-totals every
résumé that lists its roles newest-first — i.e. all of them.

**A role with no start date contributes nothing, and no dates at all yields NULL** —
guessing a start from the end date; returning 0.0 — inventing tenure is the exact
failure this stage exists to prevent, and 0.0 reads as "no experience" where NULL reads
as "we could not tell". Same polarity M1 set with `RemoteMode`.

**Seniority takes the title first and tenure only as a fallback** — tenure alone — a
promotion is a stated fact and a year count is a proxy: someone made Staff at six years
is Staff, and a career-changer with fifteen years in another field is not. Keywords are
scanned most-senior-first so "Senior Staff Engineer" resolves to Staff.

**`profiles.locations` is NOT normalized** — reusing `dedupe.normalize_location` —
M4's location filter is `profiles.locations && jobs.locations`, and `jobs.locations`
holds each source's own strings. Normalizing one side of an overlap makes matching
*worse*. This also settles the question of whether that helper needed to move into
`packages/`: M3 has no consumer for it, so it stays in the scraping stage.

**A promoted column is filled only when it is empty** — overwriting on every parse — a
person who corrected their own seniority has said something the résumé cannot
contradict. **Known ceiling:** a genuine career change leaves stale columns until the
user edits them. The alternative is tracking which fields a user has touched, which is
real state for a case that editing the profile already solves.

**`salary_floor` is never parsed.** A résumé does not state one, and inferring it from a
title is exactly the invented value this stage exists to prevent.

**The promoted columns are authoritative for anything M4 filters in SQL; `prefs_json`
holds the rest** — build-sequence.md §M3 lists "location, remote, salary floor,
must-haves" as *prefs*, which would put two of those in two places at once. Two writable
homes for one value is two places for them to disagree, and the columns are the ones
carrying the GIN index and the CHECK constraints.

### The first LLM call in the repo

**An OpenAI-compatible `httpx` call, no SDK and no `instructor`** — the openai package;
instructor — a chat-completions request is one POST, `httpx` is already a dependency,
and Celery already supplies backoff and jitter. `instructor`'s real value is unified
retry across fifteen providers and there is one here; the part worth keeping — re-asking
with the validation error attached — is ten lines, and a bare retry re-sends the same
prompt and earns the same failure.

**OpenRouter as the default base URL** — DeepSeek direct — **DeepSeek's API rejects
`response_format.type = "json_schema"` outright** ("unavailable now"), and §7.2 already
wants a cheap model for scoring and a strong one for tailoring, which is one key through
a router. `LLM_BASE_URL` keeps the choice a config change.

**The pydantic schema must be rewritten before strict mode accepts it** — passing
`model_json_schema()` straight through — strict mode requires `additionalProperties:
false` on every object and *every* property in `required`, and rejects `default`.
Pydantic emits none of the first, omits optional fields from the second, and emits the
third per defaulted field. Expressing optionality as a nullable type is exactly correct
for our models, which are already `X | None`. **The rewrite returns new dicts**:
`model_json_schema()` is cached per class, and editing it in place would corrupt every
later call in the process.

**A validation failure raises with field locations only** — the pydantic error string —
that message reaches the Celery log *and* the Redis result backend, and the value it
complains about is a fragment of somebody's résumé (Part 13 rule 9).

**`workers/llm.py` sits at the worker top level, not in a stage package** — inside
`workers/profiles/` — M4 and M5 both need it and §3.1 forbids importing across stage
packages, so a stage-local client would exist three times. Same status as `settings.py`.

### Text extraction and storage

**`PyMuPDF` is rejected on licence grounds** — despite being the fastest option —
**AGPL-3.0, whose network clause would require this service to be open-sourced or an
Artifex licence bought.** It is 8–12× faster than the alternatives at plain text
extraction and it is still the wrong answer. Recorded because somebody will otherwise
"optimise" into it.

**markitdown, not a PDF library directly** — pypdf; docling — one entry point for PDF,
DOCX and text, and it emits Markdown rather than a wall of characters, so headings and
lists survive into the model's input. Docling is also MIT but pulls torch and layout
models, which is a different order of footprint. **Accepted cost:** markitdown's *base*
dependencies include `magika`, an ONNX file-type detector, which brings `onnxruntime`
(~200 MB) into the worker image. Not optional — it is a base dependency, not an extra.

**pdfminer.six's two-column reading order is a known ceiling, not a blocker** — its
documented behaviour is to interleave columns line by line. That degrades extraction
*quality* — which the live suite measures — but **cannot corrupt the vault**, because
the model reads exactly the text claims are later verified against. A mangled layout
produces a worse record, never an unverifiable one.

**`packages/storage` is a workspace package** — a module in either app — the API writes
the upload and the worker reads it back, and `api -> workers` is forbidden. Same
reasoning that moved `record` into `packages/db` in the same milestone.

**The boto3 client is built per call, never cached at module scope** — botocore clients
are not fork-safe and Celery's prefork children would inherit one, which is the same
class of bug `db.session`'s `NullPool` exists to make structurally impossible.

**`build_key` carries a random component per upload** — a fixed name per profile —
otherwise re-uploading overwrites the object a currently-running parse task is about to
read.

### Wiring

**`record()` moved from `workers.scraping.ingest` into `packages/db`** — a second copy
in the profiles stage — M3 must write `events` rows and §3.1 forbids importing another
stage's internals. It gained a keyword-only `user_id`: ingest events are not per-user, a
profile event genuinely belongs to someone, and the column was always there.

**Parsing has no beat entry** — a periodic re-parse — it fires on upload. A schedule
would spend a model call per profile per tick to rewrite rows it already wrote.

**Only `StorageError` retries the task** — retrying every exception — an extraction
failure is deterministic (a scanned PDF does not become readable on the third attempt),
and a model that answered nonsense has already had both of `llm.MAX_ATTEMPTS` inside one
call.

**A failed parse writes `profile.parse_failed` before re-raising** — letting Celery's
log be the only record — a profile whose parse failed must not be indistinguishable from
one with an empty résumé, or M4 scores it against nothing and produces confidently wrong
matches.

**The upload endpoint clears `master_resume`** — leaving it — between that commit and
the parse finishing, stale text would be scored by M4 against a document the user has
just replaced.

**The resume router is registered before the CRUD routers** — after — both mount
`/profiles`, and `/{row_id}` would otherwise swallow the literal `/profiles/{id}/resume`.

**Unconfigured storage is a 503, not a 500** — a checkout with no bucket is a supported
state everywhere else in this repo, and should read as "this deployment cannot do that
yet" rather than as a crash.

### Defects found in shipped code

**`packages/schemas/src/schemas/profile.py`'s docstring claimed "Deliberately not
one-per-user"** — true at M0, false from migration 0003, which exists specifically to
swap `ix_profiles_user_id` for `uq_profiles_user_id`. Four other places stated the
constraint correctly; this was the only one disagreeing, and it is the first file an M3
implementer opens.

### Defects the live gate caught that 448 green tests did not

The first real run was 22/24. Both failures were in `derive.py` — our code, not the
model — and both had passing unit tests asserting the wrong thing. This is the pattern
worth remembering: **a stubbed model tests the plumbing, not the judgement.**

**An old title banded a current bandless one.** `seniority()` scanned every role and took
the first with a keyword, so a résumé whose current role is "Backend Engineer" and whose
first job was "Junior Developer" resolved to JUNIOR — six years after they stopped being
one. `_by_recency` already puts the current role first, so scanning past it bought
nothing and cost that. **Why the unit test missed it:** `test_a_title_on_an_older_role_
still_counts` *asserted the bug*. It was written to prove ordering worked, and encoded
"scan every role" as the intent rather than as an implementation detail.

**A status claim beat a sponsorship requirement in the same sentence.** A résumé reading
"EU citizen. Requires H-1B sponsorship for roles based in the United States" resolved to
CITIZEN, because status was matched before need. **This is the dangerous direction**:
§7.2 calls the work-auth filter the single most-praised feature in the leading product,
and its entire value is not showing someone jobs they cannot legally take. The reverse
error only narrows results. Need now dominates status, with negation ahead of both.
**Why the unit test missed it:** every case was a single clean phrase. Real résumés put
two facts in one sentence.

**"Remote" is not a location.** `plain.txt` says "Remote (GMT+1)" and names no city; the
model correctly declined to invent one. §3.5's filter is "location/**remote**" — two
things — and `profiles.locations` is only the place half. The label demanding "Remote"
in `locations` was wrong, and the fixture now asserts the stronger property: a résumé
that names no place produces none.

**The suite was not hermetic.** Adding a real `LLM_API_KEY` turned two green tests red
with no source change. Every interlock test is a claim about an *absent* setting, and
`monkeypatch.delenv` cannot make that true — pydantic-settings falls through to `.env`.
M2's proxy interlock test had the identical latent bug and would have failed on the
first day of real proxy credentials. Settings now come from `os.environ` only.

Two second-order traps inside that fix, both worth knowing before touching settings in a
test: `importlib.reload` re-executes a module body into the **same** module dict, so it
replaces a class object a session-scoped patch was applied to — hence the fixture is
function-scoped. And `llm.py` does `from workers.settings import get_settings` at import,
so it holds its own reference with its own `lru_cache`; clearing the module's copy is not
the same as clearing the one the code under test calls.

### Live findings

- **`EmailStr` rejects the `.test` TLD** as special-use, so the `@example.test`
  addresses the ORM-level suites use 422 through the API. API-level tests use
  `example.com`. Cost twelve failing tests to find.
- **reportlab embeds the bullet as an unmapped glyph**, so pdfminer emits `(cid:127)`.
  Real PDFs do this too. Harmless, because the vault's comparison drops everything that
  is not a letter or a digit — pinned by a test so nobody "fixes" it by loosening that
  normaliser.
- **`docx.shared.Inches` is EMU-based** and silently produced a negative reportlab frame
  width when mixed into a points-based layout. Fixture-generator only, but the failure
  mode — a unit that looks like a number — is worth knowing.

## M4 — Matching and scoring (2026-08-08)

### The pool was never seeded, and it shaped everything

`companies` held **0 of the 30 boards in `registry.SEED`**. The 39 active boards were all
M2's reverse-index finds, which is why one employer was **55% of the open pool** and a
quarter of it was German-language postings — a sampling frame that would have made the
golden set measure the wrong thing entirely.

`make seed && make ingest` fixed it with no code: **pool 1,458 → 19,707, active boards
41 → 67, that employer's share 55.3% → 5.9%.** Recorded because the next person to look
at a skewed pool should check this before concluding anything about the scrapers.

### Defects the measurement found before a line of M4 was written

**The location hard filter was a `WHERE false`.** `profiles.locations` holds `"City,
Region"` as the résumé spelled it; `jobs.locations` holds each source's own strings. Both
sides are deliberately un-normalized, and each of those decisions is individually correct
— their *composition* is the bug. Measured on the pool as it then stood:

```
profiles.locations && jobs.locations   "Portland, OR" -> 1     "Kraków, Lesser Poland" -> 0
```

Every M3 fixture profile got a candidate pool of **at most three jobs out of 1,458**, and
a matcher that returns one job and gets it right scores 100% precision. This is the
green-while-broken shape the whole milestone had to be designed around: the filter clause,
the precision clause and the cost clause all *pass* on a filter that has emptied the pool.

The filter is now coarse — remote counts, silence counts, a case-folded comma segment
counts — and the exact city goes to the model as something to weigh rather than a clause
to die on. Against the grown pool a senior Portland profile gets **4,767 candidates from
19,267**: a filter that drops three quarters, not one that drops everything. BAR.md's pool
floor and per-filter cap exist so this specific failure can never pass the gate again.

**Substring keyword matching drops 85% of the pool, or none of it.** `description ILIKE
'%go%'` matched **1,234 of 1,458** rows — "good", "going", "Google", "category", "Diego" —
against 136 for the word-boundary form. As a must-have that filter drops nothing and
silently disables §3.5's free rung; as an exclude term it drops five rows in six and still
looks like it works.

**`\m…\M` cannot wrap a term ending in punctuation.** `\mC\+\+\M` matches nothing at
all, because `\M` demands a word character and `+` is not one. Applied unconditionally,
every keyword ending in punctuation — `C++`, `.NET`, `Node.js` — becomes a must-have that
matches zero jobs. The boundaries are now conditional on the term's own first and last
character.

**An uncorrelated `unnest` subquery makes `EXISTS` true for every row.** Wrapping
`unnest(jobs.locations)` in a plain `.subquery()` unnests *every* job's locations at once,
so the location filter passed everything as soon as one job in the table matched. It was
caught by the single assertion in the suite demanding a specific row be **dropped** — every
"this should survive" assertion passed. `table_valued(...).alias(...)` was the first
attempt and is also wrong: it renders `AS job_loc` with no column list. The shipped form
is one array expression, `regexp_split_to_array(array_to_string(...))`, with no subquery
to correlate.

**The golden-set sampler re-shuffled its own round-robin.** The `filtered_out` stratum
round-robins across the filters that actually fired, so a rare one still gets picked; the
draw loop then passed that ordered list back through `_shuffled` and threw the ordering
away. The result looked entirely plausible — 15 location drops, 8 seniority — and contained
**zero** work-auth pairs, which is the filter §7.2 calls the most-praised feature in the
leading product. Work-auth-only failures do exist in the pool (22 and 43 for the two
sponsorship profiles); they were simply never reached.

### Decisions

**No cross-encoder in v1 — an explicit override of §7.2, with a trigger.** §7 asks that
changes to it be stated with a reason. The M4 gate measures precision, not architecture:
if `filter → embed → cosine top-N → LLM explain` clears the bar then the reranker is
provably unnecessary, and building it first makes the precision unattributable to any
stage. The golden run reports **recall@N** — the share of hand-labelled fit pairs that
reached the model at all — and below ~0.95 the reranker has a measured job. It would be
hosted (`cohere/rerank-v3.5` via OpenRouter, $0.001/search, where one "search" is really
25–50 real postings once a >500-token description is auto-chunked). Never self-hosted:
`bge-reranker-v2-m3` means torch in the worker image on the 4 vCPU / 8 GB box M10 also
needs for a headed Chromium.

**The model emits facts; Python computes the score.** M3's split, applied for a sharper
reason. Part 14 defers the threshold to the golden set, and a threshold only means
anything against a stable score distribution — a model-emitted 0–100 anchors on 85/90/75
and shifts wholesale with the model version, so a cut calibrated on fifty pairs measures
something else after the next bump. It also keeps the arithmetic testable with no model
calls, which leaves the paid gate pointed at the only question a live model can answer.
Cosine is recorded but is **not** a term: it decides who gets asked, not how good the
answer is, and a second weight would be fitted to fifty pairs.

**Part 14 is enforced, not documented.** `match_threshold` has no default anywhere. With
it unset the matcher records `match.skipped` and does nothing — the same interlock shape
as the aggregator without a proxy. Every milestone so far has learned that a rule written
in prose and not executed gets quietly broken.

**Salary is not a hard filter, and has no funnel counter.** §3.5 lists it, but `jobs`
carries no salary column — the only structured pay data in the repo is one feed's
`raw_json`, on 100 of 19,707 rows. A counter for a filter that structurally cannot fire
reads, in a dashboard, exactly like a filter that ran and found nothing.

**`job_embeddings.model` carries a template version (`...@v1`).** That key is what says
"this job is done". If the embedded text changes without it, the key still says done and
the table holds two incomparable vector spaces that one cosine query compares anyway.
M1's `raw_json` lesson in a new place.

**Below-threshold matches are written `skipped`, not left unwritten.** "Only
above-threshold matches proceed" becomes a database fact — M5 selects
`WHERE status = 'discovered'` and cannot see them — rather than a convention someone has
to remember. It overloads `skipped` with M6's user-initiated Skip; acceptable because the
consumer semantics are identical and `reasons_json` records which.

**The rescore's `ON CONFLICT ... WHERE status IN ('discovered','skipped')` is not
optional.** Without it a routine re-run drags an `approved` or `applied` match back to
`discovered` and undoes a human decision M6 has already messaged about.

**`seniority.py` moved to the worker top level.** §3.1 forces it: M4's filter must band
`jobs.title` and the only banding code was inside `workers/profiles/`. `RANK` is new and
necessary — **neither ordering already in the codebase is a rank.** `Seniority`'s
declaration order puts LEAD after PRINCIPAL; `TITLE_BANDS` is a match-precedence order
that puts INTERN above JUNIOR. Both look like rank, so a window comparison written against
either is wrong in a way nothing notices. LEAD and STAFF share a rank deliberately.

**The golden set is drawn from the pool, never from the matcher's own top-N**, seeded and
committed so it cannot be re-rolled until it looks convenient, with job payloads stored
beside each id so it does not rot when a posting closes. The "passes filters, low cosine"
stratum in the plan was dropped: building it would have required embedding the entire
candidate pool before a single pair was labelled — the exact spend §3.5 exists to avoid,
on a matcher whose quality is still unmeasured. A uniform draw from the candidates catches
the same retrieval false negatives, costs nothing, and is independent of the thing under
test.

**BAR.md is committed before any scored output exists**, alone and first, because
`git log --diff-filter=A` on it is the only thing that makes §9's "set **in advance**"
auditable rather than asserted. Its recall floor is the single most important line in it:
without one, threshold=100 labels nothing a good fit, precision is 1.0 by vacuity, and a
matcher that matches nothing passes the gate.


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
| `prefs_json.remote_modes` / `must_have_keywords` promoted to columns | M4's filter query needs an index on one of them. §6.2 says adding a column later is "fine"; changing one's meaning is not. | M3 |
| Re-parse overwriting a stale promoted column after a career-change re-upload | A real user hits it. The fix needs per-field "user touched this" state, which is more machinery than editing the profile. | M3 |
| Résumé versioning | M5 needs to cache tailoring per `(resume-version, JD)` — blueprint §6 implies it and nothing in the schema supports it. | M3 |
| Layout-aware extraction (docling, or a fine-tuned small model) | The live parse gate shows real two-column résumés failing. Today's ceiling is pdfminer.six's reading order. | M3 |
| A MinIO service in compose for local storage | Local development without R2 credentials becomes real friction. Today the endpoint 503s and everything else runs. | M3 |
| Seeding `prefs_json.remote_modes` from a résumé that says "Remote" | A user complains that stating "Remote" on their CV did not make remote jobs match. Deliberately not done: prefs are the user's, and a parse that wrote them would be inventing intent. | M3 |
| A cross-encoder reranker | Golden `recall@N` < ~0.95 — a hand-labelled fit pair never reached the model. Hosted only; never on the 4 vCPU / 8 GB box M10 needs for a headed browser. | M4 |
| HNSW on `job_embeddings` | Already on the model: >100k rows or p95 match query >500 ms, now measurable from `match.scored.elapsed_ms`. Deliberately not added at M4 — HNSW is approximate, and adding it now folds ANN recall loss into the very baseline the milestone exists to establish. | M4 |
| A stored profile embedding | Profile embeds exceed ~1% of a run's `embed_tokens`. Today it is one call per user per run, against a staleness rule keyed on two independently-mutating inputs. | M4 |
| A salary hard filter | A `jobs.salary_min` column exists. Today both sides are missing on 99% of the pool. | M4 |
| `jobs.locations_norm` + GIN | Pool > ~50k, or the funnel query shows up in `match.scored.elapsed_ms`. | M4 |
| A distinct below-threshold match status | M8's dashboard needs to tell "scored too low" from "the user skipped". Both mean excluded today. | M4 |
| Batching several jobs per explain call | Measured cost exceeds BAR.md's ceiling. 3–4x available, at the cost of per-job attribution and retry granularity. | M4 |
| PII retention policy for `master_resume` and `evidence` | Before the first paying customer — the same deadline Part 14 already sets for multi-tenancy isolation. These are the first genuinely private per-user rows in the schema; ICO/EDPS guidance for candidate data is 6–12 months. | M3 |
