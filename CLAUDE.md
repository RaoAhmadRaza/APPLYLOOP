# CLAUDE.md — APPLYLOOP

> Working agreement for AI-assisted development on this repository.
> Part 1 is general behaviour. Parts 2–10 are project law: architecture, invariants,
> decisions already made, and the order things get built in.
>
> **If Part 1 and Part 3 ever conflict, Part 3 wins.** The invariants exist because
> violating them causes user account bans, false claims on real résumés, or duplicate
> job applications — none of which are recoverable by a rollback.

---

# PART 1 — HOW TO WORK

Behavioural guidelines to reduce common LLM coding mistakes.

**Tradeoff:** these bias toward caution over speed. For trivial tasks, use judgement.

## 1.1 Think before coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:

- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them — don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 1.2 Simplicity first

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

**Mandatory:** invoke the `ponytail` skill (from the ponytail plugin) on **every** coding
task in this repo — writing, refactoring, fixing, reviewing, or picking a dependency. It
enforces the ladder above rather than leaving it to judgement. It governs *how much* you
build; it never overrides Part 3, which governs what is safe to build.

**Project-specific caveat:** the exceptions to "no speculative abstraction" are the
*adapter boundaries* named in §5.2 (ATS adapters, notification channels, apply methods).
Those are plural by design because we already know the second and third implementation is
coming. Everything else: single-use until proven otherwise.

## 1.3 Surgical changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:

- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it — don't delete it.

When your changes create orphans:

- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: every changed line should trace directly to the request.

## 1.4 Goal-driven execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:

- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:

```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work")
require constant clarification.

**In this repo, the success criterion for a milestone is already written down.**
See §9. Don't invent a new one; use the E2E test on the milestone's card.

## 1.5 When you are unsure whether something is safe

The three questions that change the answer to almost everything here:

1. **Does this write to the outside world?** (submit a form, send a message, post to an API)
   → It needs an idempotency key and, on the apply path, a validator gate.
2. **Does this put text on a user's résumé?** → It must be traceable to the evidence vault.
3. **Does this touch a portal that requires the user's own login?** → It never runs
   server-side. Ever. See §3.2.

If the answer to any is yes and the code you're about to write doesn't handle it, stop and say so.

## 1.6 Keep the record current

Two files carry what this one cannot, because they change every session:

| File | Holds | Update it |
|---|---|---|
| `docs/PROJECT_STATE.md` | What is *true* right now — milestone status with its evidence, what runs, what is known broken, what is unproven | **Read at the start of a session. Update before ending one.** |
| `docs/DECISIONS.md` | Decisions made *while building*, with the evidence that drove them | **When you make one.** Not at the end, when the reason has been forgotten. |

Part 7 below holds decisions made *before* building — the platform and stack choices.
`docs/DECISIONS.md` holds the ones discovered with a keyboard.

**Write an entry when someone six months from now would otherwise reasonably do the
opposite.** In practice that is:

- **A vendor's documentation was wrong.** Record the endpoint's actual behaviour and the
  date you verified it. Half of M1's and M2's adapter code exists because of these.
- **A rule had to be narrowed because live data broke it.** Record the data. "`#259` is a
  store number, not a requisition id" is worth more than the diff that fixed it.
- **You rejected the obvious approach.** Say what it was and why it loses. A future
  session will otherwise propose it again, confidently.
- **You deferred something.** It goes in the deferred table **with a trigger** — the
  condition that makes it worth doing. A deferral with no trigger is just a hole.
- **You found a defect in already-shipped code.** Record why the existing tests could not
  catch it. That is usually the more valuable half.

**Do not write an entry for:** anything the code already says plainly, anything git
history answers, a restatement of Part 7, or a narration of what you did. These files are
read by someone deciding what to do next, not audited.

**When measurement contradicts something written here**, do not quietly change this file.
Record the contradiction in `docs/DECISIONS.md` with the evidence and a trigger, and say
so. This file is law; law gets amended deliberately, not as a side effect.

---

# PART 2 — WHAT WE ARE BUILDING

## 2.1 One paragraph

APPLYLOOP is an always-on job-application engine. On a schedule, per user, it collects
fresh job listings, scores them against that user's real experience, writes a tailored
résumé and cover letter for the good matches, and then either submits the application
automatically (safe portals) or prepares it and asks the user for one tap of approval
(everything else). There is a dashboard and a browser extension.

## 2.2 The loop

```
01 Trigger   scheduler wakes the pipeline — no button press
02 Collect   pull listings from ATS APIs + aggregators into Postgres
03 Score     filter, embed, rerank, explain — cheap steps first
04 Tailor    evidence-grounded résumé + cover letter for good matches only
05 Apply     THE FORK — machine path or human path
06 Notify    Telegram/WhatsApp for anything needing a human tap
07 Track     dashboard: profiles, jobs, statuses, outcomes
```

## 2.3 What we deliberately borrowed, and from where

Three pillars are taken from the category leader (Jobright.ai, ~9 people, ~$5M ARR):

1. The job **matching/scoring engine**.
2. The AI **résumé/cover-letter tailoring**.
3. The **browser-extension autofill** — the smartest thing in their stack, because the
   submit happens in the *user's own browser*, on their real IP and session. That
   sidesteps bans **and** the per-user-browser server cost at the same time.

What we deliberately did **not** copy: their server-side "90% autonomous" apply agent,
which is still in beta and which independent testers describe as assisted, not
fire-and-forget. We treat autonomous apply as an opt-in module for low-risk portals,
architecturally isolated so its failure cannot sink the product.

## 2.4 Non-goals (do not build these unless explicitly asked)

- A job board. We do not host listings for public browsing.
- A general-purpose scraping framework. We scrape exactly the sources in §4.
- A résumé builder as a standalone product. Tailoring exists to serve the loop.
- Multi-language / i18n. English-only until told otherwise.
- Mobile apps. The phone surface is Telegram/WhatsApp.
- An agent that operates a user's LinkedIn account from our servers. Never.

---

# PART 3 — THE INVARIANTS (project law)

These are not preferences. Breaking one is a production incident, not a bug.

## 3.1 The database is the only interface between stages

Every stage reads rows from Postgres, does exactly one job, and writes rows back.
**No stage calls another stage's internals. Ever.**

```
        ┌──────────────────────────────────────────────────┐
        │              POSTGRES  (the contract)            │
        │  companies · jobs · profiles · matches ·         │
        │  documents · applications · approvals · events   │
        └──────────────────────────────────────────────────┘
   ▲writes │reads      ▲writes │reads      ▲writes │reads
   │       ▼           │       ▼           │       ▼
 ┌────────┐         ┌────────┐         ┌────────┐
 │Stage A │  ────►  │Stage B │  ────►  │Stage C │   orchestrator only sequences
 └────────┘         └────────┘         └────────┘
```

Why this is worth being strict about:

- **Independent development** — any stage can be replaced without touching the others.
- **Independent testing** — seed the DB, run the stage, assert the delta. That *is* the
  end-to-end test at every point in the build. Nothing to mock, because there's nothing
  to call.
- **Trivial wiring** — the orchestrator holds zero business logic, so swapping n8n for
  Temporal later is a scheduling change, not a rewrite.
- **Blast radius** — the riskiest module (autonomous apply) cannot reach the other eleven.

**Concretely, in code:** `apps/workers/matching/` may not import from
`apps/workers/scraping/`. Shared types live in `packages/db` and `packages/schemas`.
If you find yourself wanting a cross-stage import, the answer is a column, not an import.

## 3.2 Never auto-submit server-side on a risky portal

A **risky portal** is any portal where the application requires the user's own
authenticated account (LinkedIn, most gated enterprise ATS), or where a wrong submit is
not recoverable, or which we have not mapped and tested before.

LinkedIn and friends fingerprint device *and* IP together. A bot logged into a user's
account from our server gets that account banned. That is a product-killing complaint,
not a support ticket.

**Routing rule — a portal is `safe` only if all three hold:**

1. The application needs no login as the user.
2. The form is a known structured ATS we have a tested field map for.
3. A wrong submit is recoverable (re-appliable, or low-stakes).

Anything else routes to the human path by default. **The fallback is the human, never
the guess.** Portal risk is a first-class field on the job row, not a runtime heuristic.

## 3.3 Zero fabrication on generated documents

The model may **reorder, rephrase, and surface** evidence that already exists in the
user's evidence vault. It may **never add a claim**.

This is enforced three times, and the first one is the one people forget:

0. **Vault-side, at write time (M3)** — a claim enters `evidence` only if its text
   appears verbatim in that profile's `master_resume`. Without this, steps 1 and 2 are
   theatre: if the *parser* invents a skill, the validator below finds it, declares the
   generated bullet traceable, and puts a lie on a résumé — working perfectly and
   proving nothing. A validator is only as true as the thing it diffs against.
1. **Prompt-side** — the model is given the master résumé plus the facts store, and told
   that is the entire universe of permissible claims.
2. **Code-side** — after generation, a validator diffs every generated bullet against the
   vault. Anything not traceable to a source is stripped or returned for review, before
   the PDF is rendered.

The prompt is not the guardrail. The validator is the guardrail. Prompts regress silently;
a test does not.

**There is a permanent adversarial test** (`test_fabrication_guard`) that feeds a prompt
designed to tempt the model into inventing a skill. It must fail to get through. **This
test is never deleted, never skipped, never marked xfail.** If it is in a diff you are
writing, stop and ask.

Why this matters more than it looks: the market leader's most-documented flaw is a résumé
AI that invents skills and metrics. Users get auto-rejected, or caught lying in an
interview. This is our #1 quality *and* legal risk.

## 3.4 Applying is a write with side effects — guard it hardest

Every pipeline step is keyed by `(user_id, job_id)`. Retries must never produce a second
application.

- The `applications` row is the **lock**, not the log. Insert it before the submit
  attempt, not after.
- Use a unique constraint on `(match_id, method)` and let the database refuse duplicates.
  Don't do check-then-act in Python.
- Telegram double-taps, worker restarts mid-run, and orchestrator retries are all
  *expected*, not exceptional. Every one of them must be a no-op the second time.

## 3.5 Filter before you spend

The stage ordering is the cost architecture, not a style choice:

```
hard filters (free)  →  embeddings (cents)  →  rerank (cents)  →  LLM (real money)
```

Illustrative volume through one run:

| Stage | Jobs remaining | Cost |
|---|---|---|
| Scraped this run | 1,000 | free |
| After hard filters | 380 | free |
| After embed + rerank | 120 | cents |
| Shortlist → explained | 40 | cheap model |
| Tailored + applied | 18 | strong model |

**Never** call an LLM on something a `WHERE` clause could have eliminated. Hard filters
are location/remote, seniority band, work authorisation, salary floor, must-have keywords.

## 3.6 The build order is fixed

```
matching quality → document quality → assisted autofill → autonomous apply
```

The autonomous agent reuses the field maps and the validation logic built for the
extension. Build it first and you build it twice. See §9 for the milestone chain.

## 3.7 Cheap-to-state extras

- **Secrets** live in `.env` / the secret manager. Never in code, never in a migration,
  never in a test fixture, never in a log line.
- **The orchestrator holds no business logic.** If a piece of logic is easier to express
  as an n8n node than as Python, that is a signal the Python is badly factored, not a
  reason to put it in n8n.
- **Alert on volume, not just on errors.** A scraper that returns zero rows because a
  board changed its markup raises no exception. That is the most common real failure.

---

# PART 4 — SOURCING ARCHITECTURE

## 4.1 The five layers, in priority order

**Layer 1 — Direct ATS public JSON. The backbone. Best ROI in the entire system.**
Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee expose the same public
JSON their own careers pages call. No login, no key, no proxy, no anti-bot. First-party
freshness, working apply links, clean dedupe, overwhelmingly tech employers, free.

```
Greenhouse : https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Lever      : https://api.lever.co/v0/postings/{company}?mode=json
Ashby      : https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true
Workable   : https://apply.workable.com/api/v1/widget/accounts/{account}?details=true
SmartRecr. : https://api.smartrecruiters.com/v1/companies/{company}/postings?limit=100&offset=N
Recruitee  : https://{company}.recruitee.com/api/offers/
```

**Verified against live responses, 2026-08-06** (M1). Where a provider's own docs
disagree with its endpoint, the endpoint wins — all six adapters are built to what these
actually return:

- **Only Greenhouse exposes an update timestamp.** The other five expose creation or
  publication dates only, so change detection is a payload diff, never a timestamp
  comparison. See §6.2.
- **Greenhouse's list response has no `meta` object** (the docs promise `meta.total`) but
  *does* carry `first_published` (the docs call it detail-only). No per-job detail call
  is needed, and nothing may read `meta`.
- **Workable** — the v3 `accounts/{account}/jobs` endpoint above was replaced with the v1
  widget GET. Both are public and return the same postings; v3 needs a POST body and
  `nextPage` paging, v1 returns everything plus descriptions in one call. Also: every
  Workable job's `id` is `null` — `shortcode` is the identifier.
  **And a Workable board repeats a posting once per location** — verified 2026-08-07
  against `lawnstarter`, which returns 46 entries for 9 shortcodes. Postgres refuses an
  `ON CONFLICT DO UPDATE` that touches a row twice in one statement, so `ingest.upsert`
  collapses a batch on `external_id` before writing. None of M1's three seeded Workable
  boards repeat, which is why this only surfaced once M2's reverse-index found one.
- **SmartRecruiters is the only paginated provider, and the only one that withholds
  descriptions** from its list response. Full text needs
  `GET .../postings/{id}` per posting, so ingest fetches detail only for postings the
  upsert reports as changed.
- **Recruitee returns three non-exclusive booleans** (`remote`, `hybrid`, `on_site`),
  which is why `jobs.remote_mode` is not a boolean.

**Layer 2 — Aggregators via JobSpy.** LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter.
Catches enterprise and anything not on an ATS above. **This is the only layer that needs
proxies.** Aggregators cap ~1,000 results per search, rate-limit hard, sit behind
Cloudflare/CAPTCHA, and carry stale duplicates and ghost listings — which is exactly why
they are layer 2 and not layer 1.

**Layer 3 — Free remote-tech feeds.** Remotive, RemoteOK, Himalayas, Arbeitnow,
WeWorkRemotely, Jobicy, Working Nomads, The Muse. Open JSON, zero cost, no proxy.

```
remotive       https://remotive.com/api/remote-jobs
remoteok       https://remoteok.com/api
himalayas      https://himalayas.app/jobs/api?offset={n}&limit=20
arbeitnow      https://www.arbeitnow.com/api/job-board-api?page={n}
weworkremotely https://weworkremotely.com/remote-jobs.rss          (RSS, not JSON)
jobicy         https://jobicy.com/api/v2/remote-jobs?count=100
workingnomads  https://www.workingnomads.com/api/exposed_jobs/
themuse        https://www.themuse.com/api/public/jobs?page={n}
```

**Verified against live responses, 2026-08-07** (M2). Fixtures were recorded before any
adapter was written, and five of the eight contradict their own documentation:

- **`companyName` is the literal string `"name"` on every Himalayas posting** (and
  `companyLogo` is `"thumbnail_url"`) — placeholders that reached production. Only
  `companySlug` identifies the employer. `limit` caps at 20 and the endpoint 429s.
- **Arbeitnow does paginate** — `links` and `meta` are both present — and its
  `created_at` is epoch **seconds**. Passing that to a millisecond parser silently
  yields 1970, which is a wrong date rather than an error.
- **RemoteOK's element 0 is a legal notice**, so postings are filtered on having an `id`
  rather than by skipping index 0. It answers our own honest User-Agent, contrary to
  every write-up claiming it needs a browser one.
- **Working Nomads ships no identifier at all.** Its `external_id` is derived from the
  URL with scheme and query stripped, because a key that moves reposts the whole feed.
- **The Muse calls the title `name`** and returns the apply URL under `refs.landing_page`.

**`COMPLETE` is the flag that decides whether absence may close a row.** Only a feed
whose single pass returns its entire current listing may close by absence (remotive,
remoteok, workingnomads, weworkremotely). Everything paginated or truncated — himalayas,
arbeitnow, jobicy, themuse — ages rows out instead. Getting this wrong retires a whole
feed's history in one tick.

Attribution is a licence condition on remotive, remoteok, himalayas and jobicy. Each
adapter carries a `HOME` constant and a test asserts it.

**Layer 4 — Startup / niche.** Wellfound, YC Work at a Startup, the monthly HN hiring
thread (parseable), hiring.cafe.

**Layer 5 — Gated enterprise ATS (optional, paid).** Workday, iCIMS, Taleo,
SuccessFactors have no clean public feed. **Buy** coverage (a jobs API, or Apify ATS
actors at roughly $1–2.50 per 1,000 jobs) rather than fighting them. Do not write a
Workday scraper.

## 4.2 Dedupe

Normalise on `company + normalized_title + location`, or on canonical apply URL.

**When an aggregator job matches an existing ATS job, the ATS row wins and keeps its
apply URL.** A direct Greenhouse/Lever/Ashby URL points at a structured form our agent
and extension can handle; an aggregator redirect does not.

## 4.3 The company-slug registry — this is the moat

Direct ATS APIs return one employer per call, and **no ATS publishes a list of its
customers.** So "which company uses which ATS, under which slug" becomes *our*
infrastructure. It is annoying to build, which is precisely why it can't be cloned in a
weekend.

**Seed:** public token lists on GitHub, YC/Wellfound/Crunchbase exports, HN hiring threads
(which link Greenhouse/Lever URLs directly). A few thousand companies in days.

**Grow (this is the important part):** reverse-index from layers 2 and 3. Every job
scraped from LinkedIn or Remotive names a company → resolve its careers page → detect the
ATS → store the slug. The registry then grows itself, for free, on every run.

**Detect:**

```
detect(company):
  for ats in [greenhouse, lever, ashby, workable, smartrecruiters, recruitee]:
     url = pattern[ats].format(slug=guess_slug(company))
     if GET(url).ok and looks_like_jobs(json): return (ats, slug)
  return None   # fall back to layer 2 or 5
```

**Maintain:** poll each board on a schedule with **change detection** — ingest only new,
updated and removed roles, never a full re-write. Auto-retire slugs that 404 for N runs.
Track `jobs_last_run` per company to spot dead boards and hiring surges.

**Build vs. buy:** hit the endpoints ourselves for the best margins. Bootstrapping on a
paid ATS actor to seed coverage in days is acceptable; migrate high-volume companies to
direct pulls once seeded.

---

# PART 5 — SYSTEM ARCHITECTURE

## 5.1 Component map

```
                    ┌───────────────────────────────────────────┐
                    │            WEB APP (Next.js)              │
                    │  Dashboard · Profiles · Approvals · Auth  │
                    └───────────────┬───────────────────────────┘
                                    │ REST/tRPC
         ┌──────────────────────────┼───────────────────────────┐
         │                          │                           │
┌────────▼─────────┐   ┌────────────▼────────────┐   ┌──────────▼──────────┐
│ CORE API         │   │ ORCHESTRATOR            │   │ BROWSER EXTENSION   │
│ (FastAPI)        │   │ (n8n → Temporal later)  │   │ (Plasmo, MV3)       │
│ users, jobs,     │   │ schedules per-user      │   │ autofill + approve  │
│ matches, docs,   │   │ pipeline, retries       │   │ in user's browser   │
│ approvals, events│   └───┬───────────┬─────────┘   └─────────────────────┘
└───┬───────┬──────┘       │           │
    │       │              │           │
┌───▼──┐ ┌──▼──────┐ ┌─────▼────┐ ┌────▼──────────┐   ┌────────────────────┐
│Postgres│ │ Object │ │ Scraper  │ │ Worker pool   │   │ Notifier           │
│+pgvector│ │storage│ │ATS+JobSpy│ │(Celery/RQ):   │   │ Telegram / WhatsApp│
│ +Redis │ │(S3/R2) │ └────┬─────┘ │match, tailor, │   └────────────────────┘
└────────┘ └────────┘      │       │browser-agent  │
                      ┌────▼────┐  └──────┬────────┘
                      │ Proxies │   ┌─────▼──────────┐
                      │(residen-│   │ Browser agent  │
                      │ tial)   │   │ Skyvern /      │
                      └─────────┘   │ browser-use    │
                                    └────────────────┘
```

**Design principle:** the product backend (auth, DB, dashboard, extension API) is clean,
versioned, tested code. The orchestrator only schedules and glues. All heavy logic lives
in stateless Python workers the orchestrator calls.

This avoids the n8n complexity ceiling — silent failures past ~20 nodes, unreadable JSON
diffs, no code review, no rollback — while keeping n8n's speed for cron and notifications.

## 5.2 The three deliberate plural abstractions

Everything else is single-use until proven otherwise. These three are plural from day one
because we already know the second implementation is coming:

| Boundary | Interface | Implementations |
|---|---|---|
| **ATS adapter** | `fetch(slug) -> list[RawPosting]` | greenhouse, lever, ashby, then workable, smartrecruiters, recruitee |
| **Notification channel** | `send(user, payload) -> ChannelMessageId` | telegram, then whatsapp |
| **Apply method** | writes `applications(method=…)` | `extension`, `agent`, `manual` |

Note the third one is a *data* boundary, not a class hierarchy. The two apply paths write
to the same table with a different `method` value. Do not build an `ApplyStrategy` ABC.

## 5.3 Repo layout

```
/apps/api          FastAPI — Core API, the DB contract surface
/apps/workers      Celery workers, one package per stage
   llm.py            the one LLM call — worker infrastructure, NOT a stage, because
                     M3, M4 and M5 all need it and stages may not import each other
   /scraping         ATS adapters, JobSpy wrapper, feeds, normalizer, dedupe
   /profiles         résumé extraction, LLM parse, derived filters, evidence vault
   /matching         filters, embeddings, rerank, gap analysis
   /tailoring        LLM tailoring, fabrication validator, PDF render
   /notify           telegram, whatsapp
   /apply            browser agent, validator gate, action cache
/apps/web          Next.js dashboard
/apps/extension    Plasmo MV3 extension
/packages/db       SQLAlchemy models + Alembic migrations — the single source of truth
/packages/schemas  pydantic types shared across stages (the wire format)
/packages/storage  S3-compatible blob store — shared because the API writes the résumé
                   upload and a worker reads it back, and `api -> workers` is forbidden
/infra             docker-compose, k8s manifests later
/evals             golden set, fabrication tests, cost benchmarks
```

Cross-stage imports between `/apps/workers/*` packages are forbidden (§3.1). Both may
import from `/packages/*`.

## 5.4 Execution model

- **Per-user pipeline** on a schedule: n8n cron → webhook to Core API → enqueue Celery
  chain.
- **Fan-out:** scrape → dedupe (Redis set on `source:external_id`) → bulk embed →
  score/rerank → shortlist → tailor → route to the apply fork.
- **Idempotency:** every step keyed by `(user_id, job_id)`.
- **Backpressure:** rate-limit scraping and agent applies per proxy and per user.
  LinkedIn throttles around the tenth page per IP — pacing and rotation are mandatory,
  not optional.

## 5.5 Scaling path

1. One Docker Compose box (Hetzner CX32, 4 vCPU / 8 GB, ~€18/mo — handles browser
   automation with headroom).
2. Separate worker nodes for browser automation — it is the RAM hog (1–2 GB per browser).
3. Kubernetes with a dedicated browser pool and autoscaled scrape/tailor workers.

Migrate orchestration to Temporal the first time you need to answer *"which step failed,
for which user, three days ago, and can I replay just that step?"* and can't.

---

# PART 6 — DATA MODEL

```sql
companies    (id, name, domain, ats_type, ats_slug, last_seen_ok,
              jobs_last_run, status, consecutive_failures)
                                                  -- the slug registry, §4.3
users        (id, email, auth_id, plan, created_at)
profiles     (id, user_id, master_resume, resume_url, parsed_json, prefs_json,
              work_auth, locations[], seniority, salary_floor)
                                                  -- resume_url is a storage KEY
                                                  -- the four bare columns are what M4
                                                  -- filters in SQL; prefs_json is the rest
evidence     (id, profile_id, kind, text, source, origin)
                                                  -- the facts store half of the vault
                                                  -- every `text` appears verbatim in
                                                  -- that profile's master_resume
jobs         (id, source, external_id, title, company, company_id,
              location, locations[], remote_mode, description, url,
              ats_type, posted_at, closed_at, raw_json,
              dedupe_key, canonical_id)
                                                  -- closed_at NULL = still listed
                                                  -- canonical_id NULL = the survivor
                                                  -- the deduped pool both are NULL
job_embeddings (job_id, model, embedding halfvec)  -- pgvector, PK (job_id, model)
matches      (id, user_id, job_id, score, label, reasons_json, status)
documents    (id, match_id, type, storage_url, gdrive_url, version)
applications (id, match_id, method, status, submitted_at,
              confirmation, error)                 -- method: agent|extension|manual
approvals    (id, match_id, channel, sent_at, decided_at, decision)
events       (id, user_id, type, payload_json, created_at)
```

## 6.1 The two tables that carry the product

- **`companies`** is the registry. It is the moat. Treat schema changes here as seriously
  as an API break.
- **`matches.status`** is the state machine the entire pipeline moves through:

```
discovered → tailored → queued → approved → applied
                     ↘ skipped (reachable from any state)
```

Nothing may skip a transition. The apply stages act only on `approved` — never on
`tailored`, never on a score threshold alone.

## 6.2 Rules for touching the schema

- Every change is an Alembic migration. No exceptions, no "I'll add it manually."
- Migrations are forward-only in production. Write them reversible anyway.
- Adding a column: fine. Changing a column's meaning: that's a new column plus a backfill.
- `jobs.embedding` uses an HNSW index. Don't change the distance metric without
  re-embedding and re-running the golden set (§8.2).
- `applications` gets a unique constraint on `(match_id, method)`. See §3.4.
- Five more uniqueness rules exist for the same reason, and each has a test that proves
  the database refuses the duplicate: `jobs(source, external_id)` (ingest dedupe),
  `matches(user_id, job_id)`, `documents(match_id, type, version)`,
  `evidence(profile_id, kind, text)` — which is what makes a résumé re-parse idempotent
  without check-then-act — and a **partial** unique on
  `approvals(match_id, channel) WHERE decided_at IS NULL` — at most one *undecided*
  request per channel, so a retry cannot double-message a human.
- `profiles.user_id` is unique: **one profile per user.** `matches` is keyed on
  `(user_id, job_id)` and carries no `profile_id`, so a second profile would have
  nowhere to record its own scores. Supporting multiple target-role profiles is a real
  design change, not a relaxed constraint.
- Bounded string columns are `TEXT` + a named `CHECK` generated from a `StrEnum` in
  `packages/schemas`, never a native Postgres `ENUM`. A native enum cannot `ADD VALUE`
  inside the transaction Alembic wraps migrations in, and removing a value rewrites the
  table. Adding a value here is a drop-and-re-add of one constraint.

## 6.3 Ingest invariants (M1, M2) — do not relax any of these

- **`jobs.external_id` is always `f"{slug}:{native_id}"`.** Greenhouse integers and
  Lever/Ashby UUIDs are provably unique across tenants; Recruitee's integer `id` and
  Workable's `shortcode` are not. Without the prefix a collision makes
  `uq_jobs_source_external_id` overwrite one employer's posting with another's — the
  constraint causing corruption instead of preventing it. The bare id stays in `raw_json`.

- **`raw_json` holds exactly what the list endpoint returned, and nothing else.** It is
  the value the next run diffs against, so enriching it — merging in a detail payload,
  adding a computed field, reordering it through a model — makes every run find a
  difference, rewrite every row, and quietly stop "writing only diffs". Data from a
  second request belongs in its own column.

Three more from M2:

- **A source may only close a row by absence if one pass returns its complete current
  listing.** True of an ATS board, which returns every open role for one employer. False
  of a paginated feed, where a missing posting may simply be on a page we did not ask
  for, and false of an aggregator search, which is a query rather than a board. Those
  age rows out instead. Alongside it: an empty pass closes nothing, and a pass that
  collapses against the previous one closes nothing and records `feed.volume_drop` —
  the volume check is a **gate on the close**, not a dashboard, because by the time a
  human reads a dashboard the rows are already closed.

- **`jobs.canonical_id IS NULL` means "this row is the survivor".** Losers are marked,
  never deleted: `upsert` conflicts on `(source, external_id)`, so a deleted row has no
  conflict target and the next pass re-inserts it — delete/insert forever, and "writes
  only diffs" gone. The deduped pool every later stage reads is
  `WHERE closed_at IS NULL AND canonical_id IS NULL`.

- **Dedupe priority is a pure function of `jobs.source`.** Never of arrival order,
  `created_at`, or which task ran first. That is what makes "the survivor keeps the ATS
  apply URL" structural rather than bookkeeping — if an ATS row is open in the group it
  wins, and its `url` already *is* the ATS URL, so nothing is copied and nothing can be
  copied wrong. The normalizers behind the key are deliberately conservative: a false
  merge silently removes a real job from the pool, which is worse than a duplicate.

---

# PART 7 — DECISION LOG

Format: **decision — rejected alternative — why**. If you want to change one of these,
say so explicitly and give the reason; don't quietly do something else.

These are the decisions taken **before** building. The ones taken **while** building —
where a vendor's docs were wrong, where live data forced a rule to narrow — live in
`docs/DECISIONS.md`, which is appended to as they happen (§1.6).

## 7.1 Platform

| Layer | Decided | Rejected | Why |
|---|---|---|---|
| Web app | Next.js 15 + React 19 + Tailwind + shadcn/ui | Remix | Matches the OSS references we're forking (ResumeLM); largest hiring pool. |
| Core API | FastAPI (Python) | NestJS / one-language stack | The scraping, embedding and agent ecosystem is Python. Splitting languages to save one context switch would cost us every library. |
| Auth | Clerk, or Supabase Auth | Rolling our own | Never roll auth. Clerk = fastest MVP; Supabase if we want DB+auth+storage in one bill. |
| Database | PostgreSQL + pgvector | Postgres + separate Qdrant/Weaviate | Jobs, users, matches, events **and** embeddings in one DB means one transaction, one backup, one join. Add a dedicated vector store only if volume genuinely explodes. |
| Cache/broker | Redis | RabbitMQ, SQS | Already needed for dedupe sets and rate limits; being the Celery broker too is free. |
| Workers | Celery (or RQ) | Async tasks in the API process | Scraping, embedding and browser automation are long and RAM-heavy. They do not belong in a request. |
| Orchestrator (now) | n8n, self-hosted, **thin** | Airflow, Prefect | Free, unmetered, cron + webhook + notify in minutes. Deliberately kept thin. |
| Orchestrator (later) | Temporal | Staying on n8n | Durable retries, per-step observability, replay. See the migration trigger in §5.5. |
| Storage | Cloudflare R2 or S3, + Google Drive mirror | Storing PDFs in Postgres | Blobs don't belong in rows. Drive mirror because users expect it. |
| Deployment | Docker Compose → Kubernetes | Serverless | Browser automation needs long-lived, RAM-heavy processes. Serverless is the wrong shape. |

## 7.2 Intelligence

| Concern | Decided | Rejected | Why |
|---|---|---|---|
| Embeddings | BGE / sentence-transformers self-hosted, or hosted for MVP | Hosted only, forever | Self-hosting is free at volume; hosted is zero-ops to start. Keep the interface swappable. |
| Reranking | Cross-encoder (`bge-reranker`) on top-N | Cosine similarity alone | Large precision gain. Cosine alone produces plausible-but-wrong matches, which destroys trust faster than no matches. |
| Scoring LLM | Cheap model | Strong model everywhere | Scoring is per-job. Strong models here would dominate the bill for no measurable precision gain. |
| Tailoring LLM | Strong model, routed | Cheap model | This is the output the user's career depends on. This is the one place to spend. |
| Matching approach | Hard filters → embed → rerank → LLM explain | Keyword matching, or LLM-on-everything | Keyword alone feels bad. LLM-on-everything is unaffordable. The ladder is the point. |
| Work-auth filter | Build it in M4, not later | Deferring it | It's a cheap `WHERE` clause and it's the single feature users praise most in the leading product. Highest value-to-effort ratio in the system. |
| Résumé rendering | RenderCV (YAML→PDF) or React-PDF | LLM emits a formatted document | Deterministic, ATS-parseable, diffable. Model output that *is* the layout is unreviewable. |

## 7.3 Apply

| Concern | Decided | Rejected | Why |
|---|---|---|---|
| Risky portals | Extension in the user's browser, or approval hand-off | Server-side bot on the user's account | Bans. §3.2. Non-negotiable. |
| Safe portals | Skyvern or browser-use, **headed** Chromium | Headless | Headed is far harder to fingerprint. |
| Where the browser runs | Cloud browser (Browserbase/Steel/Hyperbrowser), or self-hosted Chrome + residential proxy | Browser per user on our servers | 1–2 GB RAM per browser. The extension exists precisely so we don't pay this. |
| Agent cost control | Cache the per-ATS action plan; replay instead of re-reason | Re-reason every time | Re-reasoning is where the tokens go — ~1,000 tokens per screenshot. |
| Submit safety | Validator re-reads every required field + screenshots, **then** submits | Trust the agent | Best vision agents reach ~85% form-fill success. Validator + human fallback takes it near 99%. |
| Extension framework | Plasmo | Raw MV3 | React + TS, auto-generated manifest, HMR, one build for Chrome/Edge/Firefox. |
| ATS support order | Greenhouse, Lever, Ashby, Workday, then iCIMS | Breadth first | The first three are also our primary sourcing layer, so their field maps pay for themselves twice — once in the scraper, once in the extension. |

## 7.4 Sourcing

| Concern | Decided | Rejected | Why |
|---|---|---|---|
| Primary source | Direct ATS public JSON | JobSpy as the backbone | Aggregators cap ~1,000/search, rate-limit, sit behind CAPTCHA, and carry ghost listings. ATS APIs are free, first-party, and give working apply URLs. |
| Proxies | Residential, **aggregator layer only** | Proxying everything | ATS APIs need none. Budget 2–5× infra cost for the parts that do use them; don't pay it for the parts that don't. |
| Gated enterprise ATS | Buy (paid API / Apify actors) | Build a Workday scraper | Bad ROI, high breakage, high legal surface. |
| Canonical apply URL | Always prefer the direct ATS URL | First-seen wins | Structured forms our tooling can handle, vs. an aggregator redirect it can't. |

## 7.5 Notifications

| Concern | Decided | Why |
|---|---|---|
| First channel | Telegram Bot API | Free, instant, inline Approve/Skip buttons, no approval friction. Working in an afternoon. |
| Second channel | WhatsApp Cloud API (or Twilio) | Needs a business number and pre-approved templates — days of lead time. Only after Telegram is proven end to end. |

---

# PART 8 — COST & OBSERVABILITY

## 8.1 Where the money goes (order of magnitude, early stage)

| Line | Cost | Note |
|---|---|---|
| Compute (Hetzner + workers) | $20–60/mo | |
| Postgres + Redis (managed, small) | $20–40/mo | |
| **Residential proxies** | $50 → hundreds | **The swing factor.** 2–5× infra cost at aggregator volume. |
| **LLM** | $0.10–0.50 per tailored application | Scoring cheap per job; tailoring is the real cost. |
| Cloud browser | ~$0.10/compute-hr | Pennies per apply, but it adds up. |
| Telegram | free | WhatsApp is per-conversation. |

**Cost discipline is architectural, not a budget setting:** hard-filter before embedding,
embed before the LLM, LLM only on the shortlist, cache tailored docs and per-ATS agent
plans. See §3.5.

## 8.2 What must be measured

- **Per-run token spend** and **per-apply cost** (Langfuse). Alert on regressions, not
  just errors.
- **Golden-set precision** (§9, M4). Gate releases on it.
- **Agent form-fill success rate** and **validator block rate** (M10).
- **Rows ingested per source per run.** A drop to zero is the most common silent failure.
- **Block rates** per proxy/board.

Stack: Sentry (errors) + Grafana/Prometheus (system) + Langfuse (LLM traces and cost).

---

# PART 9 — BUILD ORDER & MILESTONE GATES

`M1`, `M2`, `M3` depend only on `M0` and can be built in parallel. **Everything from M4
down is a strict chain.** Do not start a milestone before the one it depends on is
*proven* — not written, proven, by its E2E test.

> **CURRENT MILESTONE: M3** — profiles, résumé parsing, the evidence vault.
> M0 and M1 landed 2026-08-06; M2 landed 2026-08-07. All three gates green.
> M3 is **built** as of 2026-08-07 and green offline; its first gate clause needs one
> live run against a real model (`make verify-live-parse`, needs `LLM_API_KEY`).
> **M4 is not in scope until that run passes** — §9's rule is *proven*, not written.
> *(update this line as milestones land; it tells Claude what "in scope" means today)*

```
M0 Foundation
   ├─► M1 ATS ingestion + registry ──► M2 Aggregators + dedupe
   ├─► M3 Profiles & résumé parsing
   └─────────► M4 Matching ──► M5 Documents ──► M6 Approvals ──► M7 Orchestration
                                                                      │
                            M8 Dashboard ◄────────────────────────────┘
                                 └─► M9 Extension ─► M10 Autonomous apply ─► M11 Hardening
```

| M | Name | Gate (all must be true to move on) |
|---|---|---|
| **M0** | Foundation | One command boots the stack. `alembic upgrade head` clean, `CREATE EXTENSION vector` present. `POST /jobs` → `GET /jobs/{id}` round-trips. A pgvector similarity query returns. A no-op Celery task completes. CI green. Secrets in `.env`. |
| **M1** | ATS ingestion + registry | Three adapters green. Second run writes **only diffs** — no duplicates, removed roles marked closed. `detect()` returns the right `ats:slug` for a real careers URL. A scheduled run populates jobs unattended. |
| **M2** | Aggregators + dedupe | An overlapping company collapses to **one** row, and the survivor keeps the **ATS** apply URL. JobSpy goes through the proxy; feeds don't. Unique job count rises vs. M1 alone. |
| **M3** | Profiles | Three sample résumés parse with correct skills/seniority/location/work-auth. Prefs store and retrieve. Evidence vault populated for one test user. |
| **M4** | Matching | Golden-set precision meets the bar set **in advance** (~50 hand-labelled pairs). Hard filters demonstrably drop mismatches **before** embedding. Cost per 1,000 jobs scored is measured. Every score carries a human-readable reason. |
| **M5** | Documents | PDF opens and an ATS parser reads the fields back. **`test_fabrication_guard` passes.** Cover letter grounded only in vault evidence. Docs stored, mirrored, logged. |
| **M6** | Approvals | Telegram message arrives with docs + link. Approve advances status; Skip excludes from every apply path. Double-tap records exactly once. |
| **M7** | Orchestration | One green unattended run, cron → approval, **without touching anything**. Kill a worker mid-run: retries + idempotency prevent duplicates and the run completes. No business logic inside n8n. |
| **M8** | Dashboard | Live pipeline state visible. Approving in the UI produces the *same* state change as Telegram — one source of truth, two front doors. Playwright drives login → view → approve. **← SHIP POINT: sellable here.** |
| **M9** | Extension | Autofill correct on a **real** Greenhouse/Lever posting for an approved match. Submit logs the application with confirmation. Works across all supported ATSs. |
| **M10** | Autonomous apply | Validator passes → submits → confirmation captured. **Negative test:** a form the agent gets wrong is **blocked**, no side effect fires. Action caching lowers cost on the second apply to the same ATS. Risky portals route back to M6/M9. **← ACCEPTED RISK, isolated.** |
| **M11** | Hardening | Load test passes. Cost dashboards move. A failed run replays from history. Rate caps hold under burst. |

## 9.1 What "in scope" means

If the current milestone is M4, then writing extension field maps is **out of scope** even
if it seems easy and useful. Say so rather than doing it. The order exists because later
stages reuse earlier stages' work; doing them out of order means doing them twice.

---

# PART 10 — TESTING

| Tier | What | Where |
|---|---|---|
| **Unit** | Each ATS adapter, the normalizer, the fabrication validator, the field maps | `tests/unit` |
| **Integration** | A stage against a real Postgres (Testcontainers): seed → run → assert the DB delta | `tests/integration` |
| **Golden set (M4)** | ~50 hand-labelled (profile, job) pairs; precision tracked over time; releases gated on it | `evals/golden/` |
| **Fabrication (M5)** | Adversarial prompt the validator must catch. **Never deleted.** | `evals/fabrication/` |
| **Pipeline (M7)** | One scheduled run, no manual steps, with a failure injected mid-run to prove idempotency | `tests/e2e` |
| **Browser (M8–M10)** | Playwright for the dashboard; real-posting runs for the extension; negative test for the agent | `tests/browser` |
| **Cost (M11)** | Per-run token + proxy + browser cost; alert on regression | dashboards |

**"End-to-end" means something different at each milestone.** Early: "a source produced
rows in `jobs`." Middle: "cron fired and a Telegram approval arrived." End: "cron fired
and an application was submitted." Use the definition on the current milestone's row in §9.

**No mocking of other stages.** There's nothing to mock — seed the tables the stage reads,
run it, assert the tables it writes.

---

# PART 11 — RISK REGISTER

These risks are **accepted, not avoided**. Containment means each fails loudly in one
module without taking the product with it.

| Risk | Containment |
|---|---|
| Account bans (LinkedIn / gated ATS) | Never auto-submit server-side on a risky portal. Extension in the user's session, or approval hand-off. §3.2 |
| Scraper breakage (anti-bot, rotting selectors) | Residential proxies, pacing, headed browsers. **Monitor row volume, not just errors.** Keep JobSpy updated. |
| Résumé hallucination | Evidence-vault prompting + traceability validator that strips unbacked claims before the PDF renders. §3.3 |
| Agent mis-submits | Validator gate + screenshot before submit, idempotency keys, low-stakes portals first. §3.4 |
| Orchestrator complexity ceiling | Keep n8n thin, heavy logic in versioned code, migrate to Temporal at the trigger in §5.5. |
| Cost blowups | Filter-before-spend ordering, caching, per-user rate caps, Langfuse cost tracking. §3.5 |
| Legal / ToS | Read each board's terms. Prefer official APIs where they exist. Isolate scraping and auto-apply so a takedown hits one module. |

---

# PART 12 — PRIOR ART

Fork or study — but read §3 before importing anyone's ideas about auto-submitting.

| Project | Use |
|---|---|
| Direct ATS public JSON | Primary source. Study any OSS "ATS auto-detect" scraper for slug detection + change detection. |
| **JobSpy** | Near drop-in for the breadth layer. |
| Remotive / RemoteOK / Himalayas / Arbeitnow | Free JSON feeds. |
| **Resume-Matcher** | ATS + semantic matching, JD-aware rewriting. |
| **ResumeLM** | Next.js AI résumé builder; also the best dashboard reference. |
| **RenderCV** | Deterministic YAML→PDF. Use as-is. |
| Reactive Resume / OpenResume | Builders and parsers. Study. |
| **Skyvern / browser-use / Stagehand** | Apply engines. Pick on caching behaviour, not on stars. |
| **Plasmo** | Extension framework. Use as-is. |
| n8n → Temporal / Windmill | Orchestration now, code-first orchestration later. |

GitHub topics worth mining: `job-scraper`, `job-matching`, `resume-tailoring`,
`resume-matching`.

---

# PART 13 — THINGS TO NEVER DO IN THIS REPO

A checklist, because these are the mistakes that are cheap to make and expensive to find.

1. **Never** submit a form server-side on a portal requiring the user's own login.
2. **Never** let generated résumé text reach a PDF without passing the fabrication validator.
3. **Never** delete, skip, or `xfail` `test_fabrication_guard`.
4. **Never** import across `/apps/workers/*` stage packages.
5. **Never** put business logic in n8n.
6. **Never** call an LLM on a job a hard filter could have dropped.
7. **Never** submit without the validator gate passing (M10).
8. **Never** change the schema outside a migration.
9. **Never** hardcode a secret, including in tests and fixtures.
10. **Never** do check-then-act for application uniqueness — use the DB constraint.
11. **Never** build a later milestone before the current one's gate is green.
12. **Never** proxy the ATS layer. It doesn't need it and it costs real money.

---

# PART 14 — OPEN QUESTIONS

Decisions deliberately deferred. If a task touches one of these, ask rather than assume.

- **Match score threshold** for proceeding to tailoring — needs the golden set (M4) to set
  empirically. Do not hardcode a magic number before then.
- **Self-hosted vs. hosted embeddings** — start hosted for zero ops, revisit at volume.
  Keep the interface swappable; don't leak provider types into the matching code.
- **Portal risk classification source** — static allowlist per `ats_type` initially. Whether
  it becomes per-company or learned is undecided.
- **Pricing and plan limits** — affects rate caps in M11. Unknown.
- **Data retention** for scraped `raw_json` — currently unbounded. Will need a policy.
- **Multi-tenancy isolation level** — row-level security vs. application-level filtering.
  Decide before the first paying customer, not after.

---

*Companions: `job-automation-build-blueprint.md` (what and why) ·
`build-sequence-and-phase-architecture.md` (order and gates). This file is the operational
summary of both — where they disagree, they are the source of truth and this file is stale.*

*Living records, updated every session (§1.6): `docs/PROJECT_STATE.md` (what is true right
now) · `docs/DECISIONS.md` (what was decided while building, and why). Operational guides:
`docs/proxy-setup.md`.*
