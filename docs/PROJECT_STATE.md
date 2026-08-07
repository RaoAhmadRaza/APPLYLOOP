# Project state

**Where the build actually is.** Read this before starting a session; update it before
ending one.

CLAUDE.md §9 says what "in scope" means. This file says what is *true* — what has been
proven, with what evidence, and what is known to be broken.

> Last updated: **2026-08-07**, after M3 landed and its live gate passed.
> `main` @ `323957f` · pushed · CI green (`test` + `compose-smoke`).

---

## Milestones

| M | Name | Status | Gate evidence |
|---|---|---|---|
| **M0** | Foundation | ✅ **proven** 2026-08-06 | One command boots the stack; `alembic upgrade head` clean; pgvector present; `POST /jobs` → `GET /jobs/{id}` round-trips; a no-op Celery task completes; CI green. |
| **M1** | ATS ingestion + registry | ✅ **proven** 2026-08-06 | Six adapters green against live boards (gate asked for three). Second run writes only diffs — live PostHog board: `fetched: 12, inserted: 0, updated: 0, closed: 0`. `detect()` resolves a real careers URL. CI log reads `beat ingested 13 jobs unattended`. |
| **M2** | Aggregators + dedupe | ✅ **proven** 2026-08-07, one clause pending | See below. |
| **M3** | Profiles & résumé parsing | ✅ **proven** 2026-08-07 | Live gate **24/24 against a real model** (OpenAI, `gpt-5.4-nano` class). See below. |
| **M4** | Matching | ⬜ **unblocked — this is next** | Reads the deduped pool from M2 and the profiles from M3. |
| M5–M11 | — | ⬜ | Strict chain from M4. |

### M3 gate, item by item

| Clause | Status | Evidence |
|---|---|---|
| Three sample résumés parse with correct skills/seniority/location/work-auth | ✅ | **Four** fixtures (one more than asked), each carrying a named trap — clean single-column PDF, two-column PDF, DOCX with no Projects section, plain text with no city. `make verify-live-parse`: **24 passed** against a real model. Seniority, work-auth and location assert *exactly*; skills as a superset; years-of-experience within a labelled range. The first run was 22/24 and found two real bugs — see DECISIONS.md → M3. |
| Prefs store and retrieve | ✅ | `Prefs` schema in `packages/schemas`. Verified live against the running stack: `PATCH /profiles/{id}` with `remote_modes`, `must_have_keywords`, `exclude_keywords`, `titles` and `salary_floor` round-trips through `GET`. |
| Evidence vault populated for one test user | ✅ | `evidence` table, migration 0006 applied live. `test_profile_parse.py` asserts it is populated and that **every stored claim's text is present in `master_resume`** — the invariant that makes M5's validator worth anything. A fabricated skill is dropped and counted. |

Also true, and not asked for by the gate: a re-parse is idempotent, drops claims the new
résumé no longer makes, keeps claims the user added by hand, and never overwrites a
promoted column the user set.

### M2 gate, item by item

| Clause | Status | Evidence |
|---|---|---|
| An overlapping company collapses to one row, survivor keeps the ATS apply URL | ✅ | `tests/integration/test_dedupe.py` asserts it exactly: one survivor, `source == greenhouse`, URL starts `boards.greenhouse.io`, and `count(*) == 2` — marked, not deleted. |
| JobSpy goes through the proxy; feeds don't | 🟡 **offline half proven** | `test_aggregator.py` asserts `scrape_jobs` receives the list; `test_proxy_scope.py` asserts `http.client()` ignores all five proxy env-var spellings (verified failing before the fix). **The live half needs proxy credentials** — `test_aggregator_live.py` is written and skipped. |
| Unique job count rises vs M1 alone | ✅ | Live: Remotive added 34 rows; the reverse-index then resolved **LawnStarter → `workable:lawnstarter`** from a Remotive payload at zero request cost, and ingesting that board added 9 more from a company M1 never knew existed. |

---

## What runs today

Nine stages, all scheduled by Celery Beat, no manual step:

```
ingest_all      → ingest_company    every 6h    layer 1, six ATS providers
ingest_feed     × 8                 6–12h each  layer 3, eight free feeds
aggregate_all   → aggregate_search  every 12h   layer 2, JobSpy — NO-OPS without a proxy
grow_registry                       hourly      §4.3 reverse-index
dedupe_jobs                         every 6h    cross-source collapse
```

Plus one event-driven stage, deliberately **not** on the clock:

```
parse_profile   on résumé upload            M3 — NO-OPS without an LLM API key
```

**What M4 gets (§3.1) — two queries, no imports:**

```sql
-- the deduped pool
SELECT * FROM jobs WHERE closed_at IS NULL AND canonical_id IS NULL;

-- the profile to score it against: parsed_json + prefs_json + four promoted columns
SELECT parsed_json, prefs_json, locations, seniority, work_auth, salary_floor
FROM profiles WHERE user_id = :user_id;

-- and, for M5, the vault M3 verified
SELECT kind, text, source FROM evidence WHERE profile_id = :profile_id;
```

### Last live run (2026-08-07, local stack)

| | |
|---|---|
| Open rows | 942 |
| Deduped pool | 942 (zero false merges after the same-source fix) |
| By source | lever 807 · greenhouse 80 · remotive 34 · workable 9 · ashby 12 |
| Registry | 4 real boards, 6 negatively cached (`other`/`error`) |
| Second dedupe pass | `keyed: 0, duplicates: 0, promoted: 0` — converged |

---

## Test surface

```
451 pass, no network            make test
 12 live, all 8 real feeds      make verify-live-feeds       APPLYLOOP_LIVE_FEEDS=1
  9 live, all 6 real ATS boards make verify-live             APPLYLOOP_LIVE_ATS=1
  3 live, aggregator            make verify-live-aggregator  ← SKIPPED, needs a proxy
 24 live, résumé parsing        make verify-live-parse       ← GREEN 2026-08-07 (4 model
                                                             calls/run, well under a cent)
  3 live, object storage        make verify-live-storage     ← SKIPPED, needs a bucket
```

ruff + format + mypy clean on 133 files. Live suites are deliberately **not** in CI — a
build must not go red because a third party had a bad afternoon, layer 3 spends a metered
budget, and the parse suite spends real money.

---

## Known broken / unproven

- **`google` returns 0 rows** even unproxied from a residential IP. Rotted JobSpy selector
  or wrong search shape — **not** a blocking problem, so a proxy will not fix it. Debug or
  drop it from `aggregator.SITES` (one list entry).
- **The aggregator has never run through a real proxy.** It is interlocked: empty
  `JOBSPY_PROXIES` records `aggregate.skipped` and does nothing. That is a supported
  state, not a broken one.
- **CLAUDE.md §8.1's proxy budget is contradicted by measurement** — see
  `docs/DECISIONS.md` → *Open, deferred deliberately*. Left alone until end of project.
- **No résumé has ever been through a real bucket.** Unconfigured storage
  makes the upload endpoint 503, verified live. `test_storage_live.py` is written and
  skipped. The parse stage works without it — `master_resume` can be set directly — so
  this blocks the upload path, not the milestone.
- **The two-column PDF fixture is easier than a real one.** reportlab emits its frames
  column-major, so pdfminer recovers the reading order. A real two-column résumé from a
  word processor may not. The fixture still earns its place by proving neither column is
  *dropped*; the reading-order risk stays unmeasured until a real one arrives.
- **`POST /profiles/{id}/resume` is unauthenticated**, like every other route here. It
  accepts an upload for any profile id. M8's problem, stated so it is not discovered.

---

## Environment quirks that cost time

- `.env` is gitignored — `cp .env.example .env` before `make up`. Every M2 setting is
  optional with a working default.
- `docker compose restart` does **not** pick up code changes. Use `up -d --build`.
- Tests need Docker running; they start their own Postgres + Redis via testcontainers.
- Docker pulls are very slow on this machine (~25 min for 164 MB). They do succeed. Start
  them in the background.
- `node_modules/` and `package.json` at the repo root are the ponytail plugin, not project
  code.
- The image now installs `git`, because uv fetches the JobSpy dependency from a git sha.

---

## Open questions (CLAUDE.md Part 14) — still open

- Match score threshold for proceeding to tailoring — needs M4's golden set. **Do not
  hardcode a number before then.**
- Self-hosted vs hosted embeddings — start hosted, keep the interface swappable.
- Portal-risk classification source — static allowlist per `ats_type` initially.
- Pricing and plan limits — affects M11's rate caps.
- `raw_json` retention — currently unbounded. Will need a policy.
- Multi-tenancy isolation — RLS vs application-level filtering. **Decide before the first
  paying customer, not after.**

---

## Next

**M4 (matching)** — the first milestone in the strict chain, and what all of this was
for. It reads the three queries in *What M4 gets* above. Note §14 is still open on the
match-score threshold: it needs the golden set, so **do not hardcode a number before
then**.

Two smaller items, both blocked on credentials rather than on code:

- proxy credentials close M2's last clause (`docs/proxy-setup.md`);
- R2 credentials close M3's upload path (`make verify-live-storage`).

And one that is not blocked on anything: debug or drop the `google` aggregator site.

**Before writing M4's golden set, read DECISIONS.md → M3 → "Defects the live gate caught
that 448 green tests did not".** M4's gate is a quality bar, and the M3 lesson is that a
stubbed model tests the plumbing while the judgement stays unmeasured.
