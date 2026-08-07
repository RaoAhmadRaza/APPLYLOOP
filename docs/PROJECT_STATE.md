# Project state

**Where the build actually is.** Read this before starting a session; update it before
ending one.

CLAUDE.md §9 says what "in scope" means. This file says what is *true* — what has been
proven, with what evidence, and what is known to be broken.

> Last updated: **2026-08-07**, after M2 landed.
> `main` @ `07d90ef` · pushed · CI green (`test` + `compose-smoke`).

---

## Milestones

| M | Name | Status | Gate evidence |
|---|---|---|---|
| **M0** | Foundation | ✅ **proven** 2026-08-06 | One command boots the stack; `alembic upgrade head` clean; pgvector present; `POST /jobs` → `GET /jobs/{id}` round-trips; a no-op Celery task completes; CI green. |
| **M1** | ATS ingestion + registry | ✅ **proven** 2026-08-06 | Six adapters green against live boards (gate asked for three). Second run writes only diffs — live PostHog board: `fetched: 12, inserted: 0, updated: 0, closed: 0`. `detect()` resolves a real careers URL. CI log reads `beat ingested 13 jobs unattended`. |
| **M2** | Aggregators + dedupe | ✅ **proven** 2026-08-07, one clause pending | See below. |
| **M3** | Profiles & résumé parsing | ⬜ not started | Depends only on M0. **Can start now**, in parallel. |
| **M4** | Matching | ⬜ blocked | Needs M3. Reads the deduped pool this milestone created. |
| M5–M11 | — | ⬜ | Strict chain from M4. |

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

**The deduped pool — the entire interface M4 gets (§3.1):**

```sql
SELECT * FROM jobs WHERE closed_at IS NULL AND canonical_id IS NULL;
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
334 pass, no network            make test
 12 live, all 8 real feeds      make verify-live-feeds     APPLYLOOP_LIVE_FEEDS=1
  9 live, all 6 real ATS boards make verify-live           APPLYLOOP_LIVE_ATS=1
  3 live, aggregator            make verify-live-aggregator  ← SKIPPED, needs a proxy
```

ruff + format + mypy clean on 105 files. Live suites are deliberately **not** in CI — a
build must not go red because a third party had a bad afternoon, and layer 3 spends a
metered budget.

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

**M3 (profiles & résumé parsing)** is the only unblocked milestone and the one M4 needs.
It depends on M0 alone and touches none of M2's code.

Optional smaller items first: get proxy credentials and close M2's last gate clause
(`docs/proxy-setup.md`), or debug the `google` site.
