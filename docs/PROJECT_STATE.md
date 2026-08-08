# Project state

**Where the build actually is.** Read this before starting a session; update it before
ending one.

CLAUDE.md §9 says what "in scope" means. This file says what is *true* — what has been
proven, with what evidence, and what is known to be broken.

> Last updated: **2026-08-08**, end of day, mid-M4. The set is 121 human-confirmed pairs
> and the gate has run five times against a real model. It still **fails** — but the
> blocker has moved from **precision** to **recall**, and the two causes are located and
> written down. See *M4 gate* and *Next*.

---

## Milestones

| M | Name | Status | Gate evidence |
|---|---|---|---|
| **M0** | Foundation | ✅ **proven** 2026-08-06 | One command boots the stack; `alembic upgrade head` clean; pgvector present; `POST /jobs` → `GET /jobs/{id}` round-trips; a no-op Celery task completes; CI green. |
| **M1** | ATS ingestion + registry | ✅ **proven** 2026-08-06 | Six adapters green against live boards (gate asked for three). Second run writes only diffs — live PostHog board: `fetched: 12, inserted: 0, updated: 0, closed: 0`. `detect()` resolves a real careers URL. CI log reads `beat ingested 13 jobs unattended`. |
| **M2** | Aggregators + dedupe | ✅ **proven** 2026-08-07, one clause pending | See below. |
| **M3** | Profiles & résumé parsing | ✅ **proven** 2026-08-07 | Live gate **24/24 against a real model** (OpenAI, `gpt-5.4-nano` class). See below. |
| **M4** | Matching | 🔴 **gate run 5×, still failing — blocker moved to recall** | Set is 121 human-confirmed pairs (`human:MAR`, 0 borderline, 45 relevant). **Precision now reaches 0.80 at threshold 35 — the bar — but recall is 0.38 against a floor of 0.50.** 16 labelled-relevant pairs are rejected outright: ~11 by the model quoting non-refusals, 4 by a country-scope rule that contradicts four older labels. Both causes located, neither fixed. See DECISIONS.md → M4 → *Making the gate green* and *Two defects open*. |
| M5–M11 | — | ⬜ | Strict chain from M4. **M5 is not in scope until the golden-set bar is met.** |

### M3 gate, item by item

| Clause | Status | Evidence |
|---|---|---|
| Three sample résumés parse with correct skills/seniority/location/work-auth | ✅ | **Four** fixtures (one more than asked), each carrying a named trap — clean single-column PDF, two-column PDF, DOCX with no Projects section, plain text with no city. `make verify-live-parse`: **24 passed** against a real model. Seniority, work-auth and location assert *exactly*; skills as a superset; years-of-experience within a labelled range. The first run was 22/24 and found two real bugs — see DECISIONS.md → M3. |
| Prefs store and retrieve | ✅ | `Prefs` schema in `packages/schemas`. Verified live against the running stack: `PATCH /profiles/{id}` with `remote_modes`, `must_have_keywords`, `exclude_keywords`, `titles` and `salary_floor` round-trips through `GET`. |
| Evidence vault populated for one test user | ✅ | `evidence` table, migration 0006 applied live. `test_profile_parse.py` asserts it is populated and that **every stored claim's text is present in `master_resume`** — the invariant that makes M5's validator worth anything. A fabricated skill is dropped and counted. |

Also true, and not asked for by the gate: a re-parse is idempotent, drops claims the new
résumé no longer makes, keeps claims the user added by hand, and never overwrites a
promoted column the user set.

### M4 gate, item by item

**Latest gate run — 121 pairs, real model and embeddings:**

```
filter recall          1.00 (45/45)      bar >=0.90   PASS
cost / 1k scored       $0.303 cold       bar <=$2.00  PASS
requirement grounding  0.97 (1808/1857)               PASS
stated bars rejected   12 of 13          bars.py 10, model 6
precision              0.80 @ t=35       bar >=0.80   REACHED
recall                 0.38 @ t=35       bar >=0.50   FAIL   <- the blocker
```

**The blocker is now recall, and the cause is over-rejection.** 16 pairs labelled
relevant score 0. On this run `bars.py` produced 10 correct rejections and **0** spurious;
the model produced 6 correct and **~11** spurious — a pay disclosure, a `To apply:` URL,
"Remote work flexibility within Canada", a hedged export-control clause, a timezone window
the candidate is inside, a sponsorship refusal for someone needing no sponsorship, and
twice **the candidate's own résumé sentence**, which reaches the model because the prompt
now sends it.

**Four of the 16 are the set disagreeing with itself**, not a matcher error: BAR.md §6
rule 2 ("a remote role in a *city* the profile never named is relevant") and the
country-scope refinement approved mid-session are two versions of one rule, and the
original 64 pairs were labelled under the first while the new 57 were labelled under the
second.

| Clause | Status | Evidence |
|---|---|---|
| Golden-set precision meets the bar set **in advance** | 🟡 **precision 0.80 REACHED at t=35; recall 0.38 vs 0.50 blocks** | `evals/golden/BAR.md` was committed **alone and first**, before any scored output existed — `git log --diff-filter=A` on it is what makes "in advance" auditable. It pins precision ≥ 0.80, a recall floor, filter recall ≥ 0.90, a minimum positive count below which a run is *inconclusive*, a pool floor, a per-filter cap and a cost ceiling. `evals/golden/pairs.json` holds **121 stratified pairs, human-confirmed by `human:MAR`** — 45 relevant, 76 not_relevant, **0 borderline**, hard negatives 51 overall and 44/52 (85%) among the strata the matcher judges. Built in two passes: a model proposed 64 and a human corrected **19**; then 57 more were drawn and labelled from scratch. The reporting split went from **8 reachable positives to 24**, which is what made the bar satisfiable at all — at 8, §2's precision ≥0.80 *and* ≥8 predicted positives together demanded recall of 0.88–1.00 against a floor of 0.50. **The split is now an explicit per-pair field**, stratified over (profile, label); it used to be `scored[:20]` over a profile-grouped file, which put 80% of one résumé in the tuning half. BAR.md §3 and §7 both amended with approval, logged in its §8. Building and reviewing the set found more than it measured — see DECISIONS.md → M4. |
| Hard filters demonstrably drop mismatches **before** embedding | ✅ **offline and live** — filter recall **1.00 (45/45)** measured against the confirmed set, clearing the ≥0.90 bar. | Proven structurally rather than by a call-order spy: `test_matching.py` asserts **no `job_embeddings` row exists** for a filtered job — an embedding row is the physical receipt that a job reached a paid stage. Plus the counter chain is asserted non-increasing, and `set(ids_sent_to_llm) ⊆ set(filtered_ids)`. 34 filter tests cover all five polarity laws. |
| Cost per 1,000 jobs scored is measured | ✅ **measured: $0.303 / 1k scored, cold**, against a $2.00 ceiling. | `llm.complete_json` takes a caller-owned `usage` sink, appended **per attempt including the one that raises**; `match.scored` carries `embed_tokens`/`prompt_tokens`/`completion_tokens`. The live gate asserts `prompt_tokens > 0` **and** `embed_tokens > 0`, which is also the anti-stub proof — a fake reports zero. |
| Every score carries a human-readable reason | 🟡 **0.97 grounded (1808/1857)** — passing on the ratio, but the ungrounded tail includes the *candidate's own résumé sentence* quoted as a posting requirement, twice. A fabrication-shaped defect one milestone before M5's validator, and the reason the model's disqualifier partition is recommended for demotion to advisory. | `MatchReasons` is the `reasons_json` contract; `test_matching.py` asserts every written match has a non-empty summary and a non-empty partition. The live gate additionally asserts every requirement span is **findable in the posting** — M5's fabrication problem caught a milestone early. |

**What runs today, and what it costs:** nothing, until someone sets `MATCH_THRESHOLD`.
That is deliberate. Part 14 defers the threshold to the golden set, so there is no default
anywhere in the code — `match_all` records `match.skipped` and does nothing. Both tasks
are registered in the live worker (verified via `celery inspect registered`).


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
| Open rows | **19,707** (was 942 — see below) |
| Registry | **67 active boards** (was 41) |
| Largest single employer's share | **5.9%** (was 55.3%) |
| Distinct companies in the pool | 263 |
| `remote_mode IS NULL` | 87% — the reason every filter passes on silence |
| Candidates after hard filters, senior Portland profile | 4,767 / 19,267 |

**`registry.SEED` had never been run against this database** — all 41 boards were M2's
reverse-index finds, which is why one employer was more than half the pool. `make seed &&
make ingest` fixed it with no code change. Anyone diagnosing a skewed pool should check
this first.

---

## Test surface

```
580 pass, no network            make test
 12 live, all 8 real feeds      make verify-live-feeds       APPLYLOOP_LIVE_FEEDS=1
  9 live, all 6 real ATS boards make verify-live             APPLYLOOP_LIVE_ATS=1
  3 live, aggregator            make verify-live-aggregator  ← SKIPPED, needs a proxy
 24 live, résumé parsing        make verify-live-parse       ← GREEN 2026-08-07 (4 model
                                                             calls/run, well under a cent)
  3 live, object storage        make verify-live-storage     ← SKIPPED, needs a bucket
 10 live, the M4 gate           make verify-live-match       ← RAN 2026-08-08: 7 pass,
                                                             3 FAIL. Gate not met.
```

ruff + format + mypy clean on 149 files. Live suites are deliberately **not** in CI — a
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
- **`score()` weighs a disqualifier like a nice-to-have.** This is why M4's gate fails.
  Coverage is a flat ratio, so "ITAR: must be a U.S. person" costs one bullet out of
  fifteen and a legally impossible role scores 94. 7 of 8 false positives are `hard`
  negatives. The fix is a `disqualifiers` partition — deferred with a fired trigger.
- **The live gate is not deterministic.** Two consecutive runs, identical set and code:
  p=0.62/r=0.62 and p=0.50/r=0.50 at the same threshold. `met`/`missing` come from a model,
  so the "deterministic score" claim covers only the arithmetic half of the path.
- **`EMBED_MODEL` and `LLM_BASE_URL` are coupled and can drift apart.** A local `.env`
  pointing at OpenAI direct with the default OpenRouter slug 400s. M4 is the first code in
  the repo to call `/embeddings`, so it sat undetected. Documented in `.env.example`.
- **`make verify-live-*` can exit 0 having run nothing** when its key is absent from the
  shell — `skipif` reads `os.getenv` and `.env` is not loaded. An opt-in live run that
  finds no key should fail, not skip.
- **The pool contains 101 copies of one posting.** `Bluelight Consulting / senior software
  engineer (flask/react)`, plus Jobgether ×42, distinct `external_id`s and uncollapsed by
  `dedupe_key`. This fires M2's deferred fuzzy-dedupe trigger. The sampler works around it
  with a per-`(company, title)` cap, which still misses the same role reposted under
  punctuation variants — two Cloudflare pairs in the set are one role.
- **M3 does not always split a skills line.** `two_column.pdf` parses to three skills whose
  `name` is the whole résumé line with `keywords` empty. Anything reading `parsed_json.skills`
  must handle both shapes — **M5 will read that field to ground résumé text**.
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

**Close M4's gate.** The set is confirmed, the bar is set, the gate runs, and it fails at
one located place. In order:

1. **Give the score a notion of a disqualifier.** `MatchFacts` gains a third partition of
   verbatim spans — stated requirements whose absence is fatal (legal work status,
   language, an explicit location or timezone exclusion, a licence) — and `score()` gates
   on it instead of averaging it into coverage. Today a role a UK citizen legally cannot
   hold scores 94. **The prompt change is the risky half**: extracting disqualifiers is
   the same instruction the labelling model failed to follow on ITAR, "Mandarin required"
   and CET±3, so it needs an offline test with this session's 11 hard negatives as
   fixtures before any paid run.
2. **Re-run the gate. Expect variance** — two consecutive runs moved precision 0.62 → 0.50
   at a fixed threshold, because `met`/`missing` come from a model. A threshold worth
   pinning should hold across more than one run.
3. Only then set `MATCH_THRESHOLD`. §14 is still open on it; **do not hardcode a number**,
   and do not take one from a single run.

**Three bar-vs-reality contradictions are open and need a human**, none of them fixable by
editing a result:

- §2's pool floor (≥200/profile) is **unmeasurable** in a harness §7 requires to seed only
  stored payloads. It belongs against the real pool.
- Requirement spans can quote the résumé rather than the posting (0.97, passing on ratio).
- `career_changer` holds 1 positive in 16 pairs — the sampler is blind to which countries a
  profile may legally work in. Fixing it by relabelling would be inventing positives.

Two smaller items, both blocked on credentials rather than on code:

- proxy credentials close M2's last clause (`docs/proxy-setup.md`);
- R2 credentials close M3's upload path (`make verify-live-storage`).

And one that is not blocked on anything: debug or drop the `google` aggregator site.

**Before writing M4's golden set, read DECISIONS.md → M3 → "Defects the live gate caught
that 448 green tests did not".** M4's gate is a quality bar, and the M3 lesson is that a
stubbed model tests the plumbing while the judgement stays unmeasured.
