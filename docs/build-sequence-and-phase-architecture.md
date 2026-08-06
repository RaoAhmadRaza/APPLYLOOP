# Build Sequence & Phase Architecture

*Companion to `job-automation-build-blueprint.md`. This is the order to build in, what each phase looks like on its own, how each connects to the next, and how to test each one end-to-end before you move on.*

---

## 0. The governing idea: the database is the contract

Every phase is a **stage** that reads rows from Postgres, does one job, and writes rows back. The **schema + Core API are the only interface between phases** — no phase calls another phase's internals. This gives you three things for free:

1. **Independent development** — build/replace any stage without touching the others.
2. **Independent testing** — seed the DB, run the stage, assert the DB delta. That *is* your end-to-end test at every point in the build.
3. **Trivial wiring** — the orchestrator just calls stages in order; it holds no business logic.

```
             ┌──────────────────────────────────────────────┐
             │              POSTGRES  (the spine)            │
             │  companies · jobs · profiles · matches ·      │
             │  documents · applications · approvals · events│
             └──────────────────────────────────────────────┘
        ▲writes │reads      ▲writes │reads      ▲writes │reads
        │       ▼           │       ▼           │       ▼
   ┌─────────┐         ┌─────────┐         ┌─────────┐
   │ Stage A │  ────►  │ Stage B │  ────►  │ Stage C │   (orchestrator only sequences them)
   └─────────┘         └─────────┘         └─────────┘
```

**"End-to-end" grows as you build.** Early on, E2E = "source → rows in `jobs`." By mid-build, E2E = "cron → matched → tailored → Telegram approval." At the end, E2E = "cron → … → application submitted." Each milestone below defines the E2E slice that must be green before the next starts.

**Golden build-order rule (from the blueprint):** matching quality → document quality → assisted autofill → autonomous apply. Never build a later stage before the one it depends on is proven.

---

## 1. Dependency graph (what blocks what)

```
M0 Foundation (DB + API skeleton + Docker)
        │
        ├──► M1 ATS ingestion + registry ──► M2 Aggregators + dedup
        │                                          │
        ├──► M3 Profile & resume parsing           │
        │            │                             │
        │            └────────────┬────────────────┘
        │                         ▼
        │                   M4 Matching & scoring
        │                         │
        │                         ▼
        │                   M5 Document generation
        │                         │
        │                         ▼
        │                   M6 Notifications + approvals ──┐
        │                         │                        │
        │                         ▼                        │
        └──────────────►   M7 Orchestration (wire M1–M6)   │  ◄── first true product loop
                                  │                        │
                                  ▼                        │
                            M8 Dashboard  ◄────────────────┘
                                  │
                                  ▼
                            M9 Browser extension (assisted apply)
                                  │
                                  ▼
                            M10 Autonomous apply (safe portals)
                                  │
                                  ▼
                            M11 Hardening, scale, WhatsApp, Temporal
```

M1, M2, M3 can be built in parallel by different people (all depend only on M0). Everything from M4 down is a strict chain.

---

## 2. Milestone template

Each milestone below uses the same shape:

- **Goal / what it proves**
- **Architecture** (data flow: in → stage → out)
- **Tech stack** (only what this phase adds)
- **Interface it exposes** (the contract the next phase consumes)
- **E2E test** (the concrete slice to run green)
- **Definition of Done (DoD)** — exit gate
- **Connects to next**

---

## M0 — Foundation & skeleton (the spine)

**Goal:** a running, migratable backbone everything else plugs into. Proves the environment works before any feature exists.

**Architecture:**
```
docker-compose ─┬─ postgres (+pgvector)   ← schema + migrations
                ├─ redis                  ← queue broker + dedup sets
                ├─ core-api (FastAPI)     ← /health, CRUD for core tables
                └─ worker (Celery)        ← empty task runner, wired to redis
```

**Tech stack:** Docker Compose, PostgreSQL 16 + `pgvector`, Redis, FastAPI, SQLAlchemy + Alembic (migrations), Celery, pytest. Repo as a monorepo (`/apps/api`, `/apps/workers`, `/packages/db`, later `/apps/web`, `/apps/extension`).

**Interface it exposes:** the full DB schema (all tables from the blueprint) + a Core API with health and basic row CRUD. This is the contract for every later phase.

**E2E test:**
- `docker compose up` → all services healthy.
- `alembic upgrade head` applies cleanly; `CREATE EXTENSION vector` present.
- `POST /jobs` then `GET /jobs/{id}` round-trips a row.
- Insert a vector, run a `pgvector` similarity query, get a result.
- A no-op Celery task enqueues and completes.

**DoD:** one command boots the stack; migrations are versioned; CI runs pytest green; secrets via `.env`, never hardcoded.

**Connects to next:** M1/M2/M3 write to these tables; nothing else changes the contract without a migration.

---

## M1 — Ingestion Layer 1: direct ATS pullers + company registry

**Goal:** maximum *tech* jobs from the cleanest source, and the company-slug registry that powers it (blueprint §4, Layer 1).

**Architecture:**
```
companies (seed slugs) ─► ATS puller ─► normalizer ─► dedup ─► jobs
   ▲                        │  (Greenhouse/Lever/Ashby   (canonical    │
   │                        │   public JSON, httpx)       schema)      │
   └──── auto-detect ◄───────┘  change-detection (new/updated/removed)─┘
```

**Tech stack (adds):** Python `httpx`/`asyncio`, `pydantic` normalizer, a small ATS-adapter per provider (Greenhouse, Lever, Ashby to start), a scheduler entrypoint (cron/Celery beat). Registry lives in the `companies` table.

**Interface it exposes:** normalized rows in `jobs` (source=`ats`, canonical `apply_url`, `ats_type`) and a maintained `companies` registry. The matcher (M4) consumes `jobs`.

**E2E test:**
- Seed ~20 known tech company slugs across all three ATSs.
- Run the puller → assert ≥ N jobs land in `jobs`, all normalized to one schema, `apply_url` populated.
- Re-run → assert the second run writes **only diffs** (no duplicate rows; removed jobs marked closed).
- Feed one careers URL to `detect()` → assert it returns the correct `ats:slug`.

**DoD:** three ATS adapters green; dedup + change-detection proven; registry seed + one auto-detected company stored; a scheduled run populates fresh jobs unattended.

**Connects to next:** M2 adds more sources into the *same* `jobs` schema; M4 reads `jobs`.

---

## M2 — Ingestion Layer 2/3: aggregators + remote feeds + cross-source dedup

**Goal:** breadth beyond ATS boards (enterprise + roles not on ATSs) without duplicating what M1 already has.

**Architecture:**
```
JobSpy worker (LinkedIn/Indeed/…)  ┐
Remote feeds (Remotive/RemoteOK)   ┼─► normalizer ─► cross-source dedup ─► jobs
proxies (residential, JobSpy only) ┘        (unify to M1 schema)   (prefer ATS apply_url as canonical)
```

**Tech stack (adds):** JobSpy (Dockerized + FastAPI wrapper), residential proxy config (JobSpy layer only), free remote-feed clients (JSON). Same `pydantic` normalizer as M1.

**Interface it exposes:** more `jobs` rows, same schema. Dedup key: `company + normalized_title + location` (or canonical apply URL). When an aggregator job matches an existing ATS job, keep the ATS `apply_url`.

**E2E test:**
- Run M1 + M2 together on an overlapping company → assert the duplicate collapses to **one** row and the surviving row keeps the ATS apply URL.
- Assert JobSpy pulls through the proxy; remote feeds ingest without proxy.
- Assert total unique job count increases vs. M1 alone.

**DoD:** all sources feed one deduped table; proxies scoped to the aggregator layer only; a full ingestion run is schedulable and idempotent.

**Connects to next:** M4 now has a richer, deduped `jobs` pool to match against.

---

## M3 — Profile & resume parsing (parallel with M1/M2)

**Goal:** turn a user's master resume + preferences into the structured profile the matcher needs.

**Architecture:**
```
resume upload ─► parser ─► profiles.parsed_json (skills, titles, YoE,
prefs form   ─►           seniority, locations, work_auth) + prefs_json
```

**Tech stack (adds):** resume parser (OSS NLP parser or an LLM extraction pass), object storage (R2/S3) for the raw file, a prefs schema. Optional: an "evidence vault" store of real projects/metrics (used later by M5's zero-fabrication guardrail).

**Interface it exposes:** `profiles.parsed_json` + `prefs_json` + evidence vault. M4 reads these.

**E2E test:**
- Upload 3 sample resumes → assert parsed skills/seniority/location/work-auth are correct for each.
- Set prefs (location, remote, salary floor, must-haves) → assert stored and retrievable.

**DoD:** parsing accuracy acceptable on a small labeled set; evidence vault populated for at least one test user.

**Connects to next:** M4 matches `jobs` against `profiles`.

---

## M4 — Matching & scoring engine

**Goal:** the part that makes it feel like Jobright — accurate "Good Fit" scoring, junk filtered before you spend money (blueprint §5).

**Architecture:**
```
jobs ─┐
      ├─► hard filters ─► embed (job+profile) ─► cosine (pgvector) ─► cross-encoder rerank ─► LLM gap analysis ─► matches
profile┘  (loc/seniority/    (BGE / OpenAI)        top-N                (bge-reranker)          (score, label, reasons)
           work-auth/salary)
```
Order = cost control: hard-filter first, embed second, LLM only on the shortlist.

**Tech stack (adds):** `sentence-transformers`/BGE or OpenAI embeddings, `pgvector` index (HNSW), a cross-encoder reranker, an LLM call (cheap model) for explainable gap analysis. Fork reference: `srbhr/Resume-Matcher` + the BGE+cross-encoder+pgvector matcher.

**Interface it exposes:** `matches` rows (user_id, job_id, score 0–100, label Good/Fair/Reach, reasons_json, status=`discovered`). M5 consumes matches above threshold.

**E2E test (this one needs a quality bar, not just a pass/fail):**
- Build a **golden set**: ~50 (profile, job) pairs hand-labeled fit/no-fit.
- Run the matcher → assert precision@threshold ≥ your bar (e.g. ≥80% of "Good Fit" are truly relevant).
- Assert hard filters drop out-of-location / wrong-seniority / work-auth-mismatch before embedding.
- Assert reasons_json explains each score.

**DoD:** golden-set precision meets bar; cost per 1,000 jobs scored is measured and acceptable; only above-threshold matches proceed.

**Connects to next:** M5 tailors documents for `matches` above threshold.

---

## M5 — Document generation (resume + cover letter, zero-fabrication)

**Goal:** per-match tailored resume + cover letter that are ATS-parseable and contain **no invented claims** (the #1 quality/legal risk).

**Architecture:**
```
match + profile + evidence vault ─► LLM tailor ─► traceability validator ─► render PDF ─► store (R2 + GDrive) ─► documents
                                     (reorder/         (strip any claim not      (RenderCV /
                                      rephrase only)    in the evidence vault)     React-PDF)
```

**Tech stack (adds):** strong LLM (via OpenRouter), a validation pass that diffs generated bullets against the evidence vault, RenderCV (YAML→PDF) or a React-PDF template, Google Drive API for the mirror. Fork reference: ResumeLM.

**Interface it exposes:** `documents` rows linked to `matches` (type, storage_url, gdrive_url, version). M6/M8/M9/M10 all read these.

**E2E test:**
- Generate for a real match → assert a PDF exists, opens, and an ATS parser reads the fields back correctly.
- **Fabrication test:** inject a prompt that tempts the model to add a skill not in the vault → assert the validator strips/flags it and it never reaches the PDF.
- Cover letter grounded only in real evidence.

**DoD:** deterministic, parseable output; validator provably blocks fabrication; docs stored + mirrored + logged.

**Connects to next:** M6 sends these docs for approval.

---

## M6 — Notifications + approvals (human-in-the-loop first)

**Goal:** the reliable apply path — user approves from their phone. Build this **before** any autonomous apply.

**Architecture:**
```
match + documents ─► Telegram bot ─► [Approve | Skip] inline buttons ─► webhook ─► approvals + matches.status
```

**Tech stack (adds):** Telegram Bot API (BotFather), a webhook endpoint on Core API, `approvals` write path, status state-machine (`discovered → tailored → queued → approved/skipped → applied`).

**Interface it exposes:** `approvals` rows + status transitions. The apply stages (M9/M10) act only on `approved`.

**E2E test:**
- Trigger a match with docs → receive a Telegram message with the tailored resume + job link.
- Tap **Approve** → assert `approvals.decision=approved` and `matches.status` advances.
- Tap **Skip** → assert it's excluded from apply.

**DoD:** round-trip from match to recorded human decision works; idempotent (double-taps don't double-record).

**Connects to next:** M7 wires this into the scheduled pipeline; M8 mirrors it in the dashboard.

---

## M7 — Orchestration: wire M1–M6 into one scheduled loop

**Goal:** the **first true end-to-end product** — one timer fires and the whole assisted loop runs with zero manual steps.

**Architecture:**
```
n8n cron ─► webhook /run?user=X ─► Core API enqueues Celery chain:
   ingest(M1,M2) → match(M4) → tailor(M5) → notify(M6)
   with idempotency keys (user_id, job_id) + retries + per-user schedule
```

**Tech stack (adds):** n8n (self-hosted, thin — schedule + webhook + notify only), Celery chains/groups, idempotency keys, a `events` audit trail. (Migrate orchestration to Temporal in M11 when durability/observability demand it.)

**Interface it exposes:** a per-user scheduled pipeline. Every stage remains independently runnable; n8n only sequences.

**E2E test (the big one):**
- Configure a test user + schedule.
- Let the cron fire (or trigger manually) → assert: jobs ingested → matches created → docs generated → Telegram approval received — **all without touching anything**.
- Kill a worker mid-run → assert retry/idempotency prevents duplicates and the run completes.

**DoD:** one green unattended run start-to-approval; retries + idempotency proven; no business logic living inside n8n.

**Connects to next:** M8 puts a UI on top of the loop that's now running.

---

## M8 — Dashboard (Next.js)

**Goal:** the screen the blueprint promises — profiles, discovered jobs + scores, generated docs, statuses, approvals queue.

**Architecture:**
```
Next.js app ──REST/tRPC──► Core API ──► Postgres
  pages: Profiles · Jobs(+score/label) · Documents · Approvals queue · Application status
```

**Tech stack (adds):** Next.js 15 + React 19 + Tailwind + shadcn/ui, Clerk/Supabase auth, tRPC or REST client. Reuse ResumeLM as a UI reference.

**Interface it exposes:** a human control surface over the same DB; approvals from UI mirror the Telegram path.

**E2E test:**
- Log in → see the jobs/matches/documents produced by an M7 run.
- Approve a match in the UI → assert same state change as the Telegram path (one source of truth).
- Playwright test drives login → view → approve.

**DoD:** dashboard reflects live pipeline state; UI and Telegram approvals are consistent; auth works.

**Connects to next:** M9 adds the browser extension that submits approved applications.

---

## M9 — Browser extension (assisted autofill)

**Goal:** the Jobright-style sticky core — one-click autofill in the **user's own browser**; user clicks submit (safe, no ban risk).

**Architecture:**
```
Plasmo extension (content script) ─► detects ATS ─► pulls profile + tailored doc from Core API
   ─► autofills fields (mimic typing) ─► USER submits ─► reports status ─► applications
```

**Tech stack (adds):** Plasmo (React + TS, MV3), content scripts with per-ATS field maps (start with top 3–5: Greenhouse, Lever, Ashby, Workday, Workable), Core API endpoints for the extension, Chrome Web Store dev account.

**Interface it exposes:** `applications` rows (method=`extension`, status, confirmation). Shared with the pipeline so the dashboard shows submitted apps.

**E2E test:**
- Load the extension → open a **real** Greenhouse/Lever posting for an approved match → click autofill → assert every mapped field is correct → submit → assert `applications` logged with confirmation.
- Test on all 3–5 supported ATSs.

**DoD:** top ATSs autofill reliably; status round-trips to dashboard; "user submits" flow is solid on real postings.

**Connects to next:** M10 automates the submit step for *safe* portals only, reusing these field maps + validation.

---

## M10 — Autonomous apply (safe portals only — the risky differentiator)

**Goal:** true fire-and-forget submission for low-risk portals, gated hard. This is last on purpose (highest risk, depends on everything above).

**Architecture:**
```
approved match (safe portal, score ≥ worth-it) ─► browser agent (headed) ─► validator gate ─► submit ─► confirmation ─► applications
   Skyvern/browser-use on cloud browser + residential proxy
   per-ATS action caching (replay instead of re-reason)
```

**Tech stack (adds):** Skyvern or browser-use, a cloud browser (Browserbase/Steel/Hyperbrowser) or self-hosted headed Chrome + proxy, an action-plan cache (Stagehand-style), a **validator** that re-reads the form + screenshots before submit.

**Interface it exposes:** `applications` rows (method=`agent`). Same table as M9 — the apply fork just has two branches now.

**E2E test:**
- On a safe/sandbox portal: agent fills → **validator passes** → submits → confirmation captured in `applications`.
- **Negative test:** feed a form the agent will get wrong → assert the validator **blocks** submit (no side-effect fires).
- Assert action caching cuts token cost on the second apply to the same ATS.
- Risky portals (LinkedIn) route to M6/M9 (never here).

**DoD:** validator provably prevents bad submits; caching lowers cost; only safe portals + above-threshold + approved matches reach this stage.

**Connects to next:** M11 hardens and scales the whole system.

---

## M11 — Hardening, scale & second channel

**Goal:** production resilience, cost control, and growth.

**Adds:** Sentry + Langfuse (per-run token cost, agent success rate) + Grafana/Prometheus; per-user rate caps + backpressure; WhatsApp Cloud API channel (after Telegram is proven); **Temporal** migration for durable orchestration; Kubernetes with a dedicated RAM-heavy browser pool; broaden ATS + board coverage; referrals/insider-connections layer.

**E2E test:** load test the pipeline; verify cost dashboards; replay a failed run from history; confirm rate caps hold under burst.

**DoD:** observable, cost-bounded, horizontally scalable, multi-channel.

---

## 3. Tech stack per phase (at a glance)

| Milestone | New stack introduced |
|---|---|
| **M0 Foundation** | Docker Compose, Postgres+pgvector, Redis, FastAPI, SQLAlchemy+Alembic, Celery, pytest |
| **M1 ATS ingestion** | httpx/asyncio, pydantic normalizer, ATS adapters (Greenhouse/Lever/Ashby), Celery beat |
| **M2 Aggregators** | JobSpy (Docker+FastAPI), residential proxies, remote-feed clients |
| **M3 Profiles** | Resume parser (NLP/LLM), R2/S3 storage, evidence vault |
| **M4 Matching** | BGE/OpenAI embeddings, pgvector HNSW, cross-encoder reranker, LLM (cheap) |
| **M5 Documents** | Strong LLM via OpenRouter, traceability validator, RenderCV/React-PDF, Google Drive API |
| **M6 Notifications** | Telegram Bot API, webhook, status state-machine |
| **M7 Orchestration** | n8n (thin), Celery chains, idempotency, events audit |
| **M8 Dashboard** | Next.js 15/React 19/Tailwind/shadcn, Clerk/Supabase auth, tRPC/REST, Playwright |
| **M9 Extension** | Plasmo (React+TS, MV3), per-ATS field maps, Chrome Web Store |
| **M10 Autonomous apply** | Skyvern/browser-use, cloud browser (Browserbase/Steel/Hyperbrowser), proxies, action cache, validator |
| **M11 Hardening** | Sentry, Langfuse, Grafana/Prometheus, WhatsApp Cloud API, Temporal, Kubernetes |

---

## 4. How the phases connect (the contracts)

| From → To | Contract passed (DB/API) |
|---|---|
| M1/M2 → M4 | `jobs` (normalized, deduped, canonical apply_url) |
| M3 → M4 | `profiles.parsed_json` + `prefs_json` + evidence vault |
| M4 → M5 | `matches` above threshold (score, label, reasons) |
| M5 → M6/M8/M9/M10 | `documents` (linked to match, PDF in R2 + GDrive) |
| M6/M8 → M9/M10 | `approvals` + `matches.status = approved` |
| M9/M10 → M8 | `applications` (method, status, confirmation) |
| M7 | sequences M1→M6 per user on a schedule; touches no internals |

Every arrow is a **row in a table**, not a function call between phases. That's what keeps phases swappable and independently testable.

---

## 5. Testing strategy per layer

- **Unit** — each ATS adapter, the normalizer, the validator, field maps.
- **Integration** — stage against a real Postgres (Testcontainers): seed → run → assert DB delta.
- **Golden-set eval (M4)** — hand-labeled (profile, job) pairs; track precision over time; gate releases on it.
- **Fabrication test (M5)** — adversarial prompt must be caught by the validator; this test never gets deleted.
- **E2E pipeline (M7)** — one scheduled run, no manual steps, with a mid-run failure injected to prove idempotency/retries.
- **Browser E2E (M8/M9/M10)** — Playwright for dashboard; real-posting runs for the extension; validator negative-test for the agent.
- **Cost/observability (M11)** — per-run token + proxy + browser cost dashboards; alert on regressions.

---

## 6. Recommended sequencing summary (one line each)

1. **M0** stand up the spine (DB + API + Docker) and prove it.
2. **M1** pull tech jobs from ATS APIs + build the slug registry.
3. **M2** add JobSpy + remote feeds, dedup across all sources.
4. **M3** parse resumes/profiles (parallel with M1/M2).
5. **M4** match + score to a measured quality bar.
6. **M5** generate tailored docs with zero fabrication.
7. **M6** notify + capture human approval (the reliable path).
8. **M7** wire it all into one scheduled, unattended loop ← first real product.
9. **M8** put the dashboard on top.
10. **M9** ship the autofill extension (assisted submit).
11. **M10** add autonomous apply for safe portals, behind a validator.
12. **M11** harden, add WhatsApp/Temporal/K8s, scale.

**Ship point:** you have a sellable product at **M8** (match + tailor + notify + dashboard) and a strong one at **M9** (assisted autofill). M10 is the differentiator you add once the reliable core is proven — exactly the order Jobright's own experience says to follow.

---

*End of build sequence.*
