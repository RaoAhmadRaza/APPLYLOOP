# Job-Application Automation Platform — Technical Build Blueprint

*A Jobright-inspired system: scrape → match → tailor → apply (forked) → notify → dashboard.*

---

## 1. What we're building

An always-on system that, per user profile, runs this loop on a schedule:

1. **Trigger** — scheduler wakes the pipeline. No button press.
2. **Collect** — a dedicated scraper pulls fresh listings from many boards fast/cheap into a database.
3. **Score** — each listing is matched against the user's profile (skills, location, seniority). Junk is dropped before spending money on it.
4. **Tailor** — for good matches, AI rewrites the resume for that specific job and writes a cover letter; both saved to storage + logged.
5. **Apply (forked)**:
   - **Safe portals** → a browser-agent module opens the page, fills the form, validates, submits.
   - **Risky portals (LinkedIn, etc.)** → prepare everything, do **not** auto-submit; hand off for approval.
6. **Notify** — WhatsApp/Telegram ping for anything needing a human tap ("application ready — approve?").
7. **Dashboard** — one screen for profiles, discovered jobs, applied/queued/approved status, and outcomes.

We take direct inspiration from Jobright.ai for three proven pillars: **(a)** the job-matching/scoring engine, **(b)** the AI resume/cover-letter tailoring, and **(c)** the **browser-extension autofill** model — which is the single smartest thing in their stack, because the apply happens in the *user's own browser* (real IP, real session), sidestepping both bans and per-browser server cost.

> **Strategic note carried from research:** Jobright is a ~9-person team at ~$5M ARR. Their "90% autonomous auto-apply agent" is the one part still stuck in beta — independent testers call it "assisted apply, not fire-and-forget." So we treat the **extension + human-approval** path as the reliable core, and the **fully autonomous browser agent** as an opt-in module for low-risk portals only. You've accepted the risk; the architecture still isolates it so a failure there can't sink the product.

---

## 2. High-level architecture

```
                         ┌───────────────────────────────────────────┐
                         │            WEB APP (Next.js)               │
                         │  Dashboard · Profiles · Approvals · Auth   │
                         └───────────────┬───────────────────────────┘
                                         │ REST/tRPC
              ┌──────────────────────────┼───────────────────────────┐
              │                          │                           │
   ┌──────────▼─────────┐   ┌────────────▼────────────┐   ┌──────────▼──────────┐
   │  CORE API (FastAPI)│   │  ORCHESTRATOR           │   │  BROWSER EXTENSION   │
   │  users, jobs,      │   │  (n8n → Temporal later) │   │  (Plasmo, MV3)       │
   │  matches, docs,    │   │  schedules per-user     │   │  autofill + approve  │
   │  approvals, events │   │  pipeline, retries      │   │  in user's browser   │
   └───┬───────┬────────┘   └───┬───────────┬─────────┘   └──────────────────────┘
       │       │                │           │
 ┌─────▼──┐ ┌──▼───────┐  ┌─────▼────┐ ┌────▼─────────┐   ┌────────────────────┐
 │Postgres│ │ Object   │  │ Scraper  │ │ Worker pool  │   │ Notifier           │
 │+pgvector│ │ storage │  │ service  │ │ (Celery/RQ): │   │ Telegram / WhatsApp│
 │  +Redis │ │(S3/R2/  │  │ATS+JobSpy│ │ match, tailor│   │ Cloud API          │
 └────────┘ │ GDrive) │  └────┬─────┘ │ browser-agent│   └────────────────────┘
            └─────────┘       │       └──────┬───────┘
                         ┌────▼────┐   ┌──────▼─────────┐
                         │ Proxies │   │ Browser agent  │
                         │(residen-│   │ Skyvern/       │
                         │ tial)   │   │ browser-use +  │
                         └─────────┘   │ cloud browser  │
                                       └────────────────┘
```

**Design principle:** keep the *product backend* (multi-tenant app: auth, DB, dashboard, extension API) as clean code. Use the *orchestrator* only to schedule and glue the per-user pipeline, and push all heavy/custom logic (scraping, matching, tailoring, browser-agent) into **stateless Python workers** the orchestrator calls. This avoids the n8n "spaghetti monolith" ceiling (silent failures past ~20 nodes, unreadable JSON diffs) while keeping n8n's speed for scheduling + notifications.

---

## 3. Recommended tech stack (per layer)

| Layer | Primary choice | Why / alternatives |
|---|---|---|
| **Web app / dashboard** | **Next.js 15 + React 19 + Tailwind + shadcn/ui** | Same stack as most OSS references (ResumeLM). Alt: Remix. |
| **Core API** | **FastAPI (Python)** | Python ecosystem for scraping/ML/agents lives here. Alt: NestJS if you want one language. |
| **Auth** | **Clerk** or **Supabase Auth** / **Auth.js** | Clerk = fastest MVP; Supabase if you want DB+auth+storage in one. |
| **Database** | **PostgreSQL + `pgvector`** | Jobs, users, matches, events **and** embeddings in one DB. Add **Qdrant/Weaviate** only if vector volume explodes. |
| **Cache / queue broker** | **Redis** | Dedup sets, rate limiting, Celery/RQ broker. |
| **Task workers** | **Celery** or **RQ** (Python) | Long jobs: scrape, embed, tailor, browser-apply. |
| **Orchestrator (MVP)** | **n8n (self-hosted, Docker)** | Schedule triggers, notify, light glue. Free, unmetered self-hosted. |
| **Orchestrator (scale)** | **Temporal** or **Windmill** | Durable, code-first, real retries/observability once flows get complex. |
| **Primary source (tech)** | **Direct ATS public JSON APIs** (Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee) | First-party tech jobs at scale. No login, no proxy, no anti-bot. Cleanest data + real apply links. See §4. |
| **Aggregator source** | **JobSpy** (`speedyapply/JobSpy`), Dockerized + FastAPI | Multi-board breadth: LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter. API-key auth, rate limiting, proxy support. |
| **Remote-tech feeds** | **Remotive, RemoteOK, Himalayas, Arbeitnow, WeWorkRemotely, Jobicy** | Free JSON feeds, remote-tech heavy. Zero-cost breadth. |
| **Proxies** | **Residential/rotating** (IPRoyal, Bright Data, Lightning) | Needed **only** for the aggregator layer (LinkedIn etc.); ATS APIs need none. Budget **2–5× infra cost** for the parts that use them. |
| **Embeddings (matching)** | **BGE / `sentence-transformers`** (self-host) or **OpenAI `text-embedding-3`** | Self-host = free at volume; OpenAI = zero-ops for MVP. |
| **Reranking** | **Cross-encoder** (`bge-reranker`) | Big precision boost over pure cosine similarity. |
| **Resume matching (OSS to fork)** | **Resume-Matcher** (`srbhr/Resume-Matcher`) | ATS keyword + semantic match + rewrite. |
| **Resume tailoring / build** | **ResumeLM** (`olyaiy/resume-lm`) + **RenderCV** | ResumeLM = AI builder (Next.js, OpenAI/Anthropic/Gemini). RenderCV = YAML→PDF "resume as code" for deterministic programmatic output. Alt: Reactive Resume, OpenResume. |
| **LLM (tailoring/scoring reasoning)** | **Claude / GPT-4-class via API**, router via **OpenRouter** | Use a cheaper model for scoring, stronger model for final resume/cover copy. |
| **Browser agent (safe-portal apply)** | **Skyvern** (`AGPL`, self-host) or **browser-use** | Skyvern = vision + code, handles auth/2FA/CAPTCHA, cacheable workflows, best-in-class form-fill. browser-use = Python, biggest community. **Stagehand** if you want caching→near-zero repeat cost. |
| **Cloud browser (if not self-hosting Chrome)** | **Browserbase / Steel / Hyperbrowser** | Managed headed browsers, stealth, proxies. ~$0.10/compute-hr class. Avoids the 1–2 GB-RAM-per-browser tax. |
| **Autofill extension** | **Plasmo** (React + TS, MV3, cross-browser) | The Jobright-style path: fill in the user's real browser; they click submit. |
| **Notifications** | **Telegram Bot API** (free, instant) + **WhatsApp Cloud API** (Meta) or **Twilio** | Telegram first (trivial); WhatsApp needs a Meta business number + template approval. |
| **Object storage** | **Cloudflare R2 / S3** + optional **Google Drive API** | Store generated PDFs; GDrive mirror because users like it (Jobright does this). |
| **Observability** | **Sentry** + **Grafana/Prometheus** + **Langfuse** (LLM traces/cost) | Track failures, per-run token spend, agent success rate. |
| **Deployment** | **Docker Compose** (MVP) → **Kubernetes** (scale). Host: **Hetzner** (cheap) / **Fly.io** / **Railway** | Hetzner CX32 (4 vCPU/8 GB, ~€18/mo) handles browser automation with headroom. |

---

## 4. Job sourcing strategy (tech-first) — the ATS-API-first stack

**Goal right now: maximum tech jobs.** JobSpy alone is not enough — it only covers aggregator boards, which cap ~1000 results per search, rate-limit hard, and sit behind Cloudflare/CAPTCHA with stale, duplicate, ghost listings. For tech volume the highest-leverage source is **direct ATS public JSON APIs**, which JobSpy doesn't touch.

### The 5 sourcing layers (in priority order)

**Layer 1 — Direct ATS APIs (backbone, best ROI).** Greenhouse, Lever, Ashby, Workable, SmartRecruiters, Recruitee expose the *same public JSON their own careers pages call* — no login, no key, no proxy, no anti-bot, first-party freshness, working apply links, clean dedupe. Overwhelmingly tech companies (Stripe, Airbnb, Figma, Anthropic, Databricks, Cloudflare, Reddit, Discord, Datadog, Notion, Linear, Perplexity, Cursor, Ramp, Netflix, Spotify, Coinbase, and thousands more). Free to hit directly from your own code.

Exact endpoints:
```
Greenhouse : https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
Lever      : https://api.lever.co/v0/postings/{company}?mode=json
             (filters: team, department, location, commitment, level)
Ashby      : https://api.ashbyhq.com/posting-api/job-board/{name}?includeCompensation=true
Workable   : https://apply.workable.com/api/v3/accounts/{account}/jobs   (POST, public)
SmartRecr. : https://api.smartrecruiters.com/v1/companies/{company}/postings
Recruitee  : https://{company}.recruitee.com/api/offers/
```

**Layer 2 — JobSpy (aggregator breadth).** LinkedIn, Indeed, Glassdoor, Google, ZipRecruiter. Catches enterprise + roles not hosted on the ATSs above. This is where proxies are actually required.

**Layer 3 — Remote-tech free feeds.** Remotive, RemoteOK, Himalayas, Arbeitnow, WeWorkRemotely, Jobicy, Working Nomads, The Muse — all publish free JSON, remote/tech-heavy.

**Layer 4 — Startup / niche.** Wellfound (AngelList), YC "Work at a Startup," the monthly Hacker News "Who is hiring" thread (parseable), hiring.cafe.

**Layer 5 — Long-tail / gated enterprise ATS (optional, paid).** For Workday, iCIMS, Taleo, SuccessFactors (no clean public feed): a paid aggregator (JSearch, Coresignal, Adzuna free tiers) or Apify ATS actors (~$1–2.50 per 1,000 jobs).

**Then dedup across all layers** — normalize on `company + normalized_title + location`, or on canonical apply URL — so the same role from Greenhouse and LinkedIn collapses to one row. Prefer the **direct-ATS apply URL** as canonical: it points at a structured Greenhouse/Lever/Ashby form your browser-agent and extension handle far better than an aggregator redirect.

### The one hard part: the company-slug registry

Direct ATS APIs return one employer per call, and **no ATS publishes a list of its customers.** So maintaining "which tech company uses which ATS + their slug" becomes *your* infrastructure — and that registry is a real part of your moat. How to build and keep it fresh:

**Seeding (get to a few thousand tech companies fast):**
- Public seed lists: GitHub repos and Gists that already enumerate Greenhouse/Lever/Ashby board tokens (search "greenhouse board tokens list", "lever companies list"); several are actively maintained.
- Reverse-index from Layer 2/3: every job you scrape from LinkedIn/Indeed/Remotive names a company — resolve each company's careers page and detect its ATS (see auto-detect below), then store the slug.
- Startup directories: YC company list, Wellfound, Crunchbase/BuiltWith exports, "top tech companies" lists → probe each for an ATS board.
- Tech-signal sources: HN "Who is hiring" and tech Twitter/Discord frequently link Greenhouse/Lever/Ashby URLs — parse the slugs out.

**Auto-detection (turn a company/careers URL into `ats:slug`):** probe the known ATS URL patterns for the company's name/domain and keep whichever returns valid JSON. This is exactly what the "auto-detect the ATS from a careers URL" scrapers do — replicate it:
```
detect(company):
  for ats in [greenhouse, lever, ashby, workable, smartrecruiters, recruitee]:
     url = pattern[ats].format(slug=guess_slug(company))
     if GET(url).ok and looks_like_jobs(json): return (ats, slug)
  return None   # falls back to Layer 2/5
```

**Maintenance (keep it alive):**
- Store `companies(name, domain, ats_type, ats_slug, last_seen_ok, jobs_last_run, status)`.
- Poll each active board on a schedule; run **diff/change-detection** so you only ingest new/updated/removed jobs (the good ATS scrapers all do this — new vs. recurring vs. closed).
- Auto-retire slugs that 404 for N runs; auto-promote newly discovered ones from the reverse-index.
- Track `jobs_last_run` per company to spot dead boards and hiring surges.

**Build vs. buy:** you can hit these endpoints yourself for free (best margins, more maintenance), or start on an Apify ATS actor / a jobs API to bootstrap coverage in days, then migrate the high-volume companies to your own direct pulls once the registry is seeded. For the MVP, seed a few hundred well-known tech companies by hand + a public list, pull them directly, and grow the registry from the reverse-index automatically.

---

## 5. The matching & scoring engine (Jobright-inspired)

The part that makes the product *feel* good. Do **not** rely on keyword matching alone.

**Pipeline per (user, job):**

1. **Parse** the user's master resume once into a structured profile (skills, titles, YoE, seniority, location, work-auth). Use a resume parser (OSS: OpenResume / `resume-parser` NLP libs) or an LLM extraction pass.
2. **Hard filters (cheap, first):** location/remote, seniority band, work authorization (H1B/visa flag — Jobright's unique differentiator; replicate it), salary floor, must-have keywords. Drop non-matches now — this is what saves LLM spend.
3. **Semantic score:** embed the job description and the profile (BGE/`sentence-transformers`), compute cosine similarity in `pgvector`.
4. **Rerank:** run a **cross-encoder** on the top-N to get a sharper relevance score.
5. **Explainable gap analysis (LLM, only on shortlist):** "you match 8/10 requirements; missing X, Y." Produces the "Good Fit / Fair / Reach" label and a 0–100 score like Jobright's compatibility score.
6. **Store** score + reasons on the `match` row. Only matches above a threshold proceed to tailoring.

**Reference to fork:** the OSS "BGE embeddings + cross-encoder reranking + pgvector + FastAPI + JobSpy" matcher is essentially this exact architecture already assembled.

---

## 6. AI resume + cover-letter tailoring

**Non-negotiable design rule: zero fabrication.** Every OSS project in this space converged on this, because the market leader's biggest documented flaw is its resume AI *inventing* skills/metrics not in the original. Users get auto-rejected or caught lying.

**How to enforce it:**
- Feed the LLM the user's **evidence vault** (master resume + a facts store of real projects/metrics). Instruct it to **only reorder, rephrase, and surface** existing evidence to match the JD — never add new claims.
- Post-generate **validation pass**: diff generated bullets against the evidence vault; flag any claim not traceable to source and strip/return it for review.
- Output through **RenderCV** (YAML→PDF) or a React-PDF template so formatting is deterministic and ATS-parseable.

**Reference to fork/study:** ResumeLM (AI builder), Resume-Matcher (JD-aware rewrite), and the several "evidence-backed / zero-fabrication" tailoring repos on GitHub's `resume-tailoring` topic.

**Cover letter:** same evidence-grounded prompt, one strong model, cached per (resume-version, JD).

---

## 7. The apply fork

### 6a. Safe / simple portals → autonomous browser agent
- **Engine:** Skyvern or browser-use, driving a **headed** Chromium (headed = far harder to fingerprint than headless).
- **Run location:** a cloud browser (Browserbase/Steel/Hyperbrowser) or your own Chrome on a VPS with residential proxy.
- **Flow:** load posting → agent fills fields from profile → **validator step** (re-read the form, confirm every required field is correct, screenshot) → submit → capture confirmation → log to `application`.
- **Cost control:** cache the per-ATS action plan (Stagehand-style) so repeat applies on the same ATS replay deterministically instead of re-reasoning (which is where the token cost lives — ~1000+ tokens/screenshot). Only invoke the agent for jobs above a "worth it" score.
- **Reliability reality:** best vision agents ~85% form-fill success; add the validator + human fallback to approach ~99%. Never let it submit without the validator passing.

### 6b. Risky portals (LinkedIn, big ATS behind login) → prepare + hand off
- **Do not** run a bot logged into the user's LinkedIn from your servers — they fingerprint device+IP and ban.
- Instead: generate the tailored resume + draft message + pre-filled application payload, and push it to the **browser extension** so the user submits from their own session, **or** create a draft/notification for one-tap approval.
- This is exactly Jobright's model and the reason their autofill is the "sticky," well-rated component while their server-side agent isn't.

---

## 8. The browser extension (the Jobright-style core)

**Framework:** Plasmo (React + TypeScript, auto-generates MV3 manifest, HMR, builds for Chrome/Edge/Firefox).

**What it does:**
- Content script detects the ATS (Workday, Greenhouse, Lever, Ashby, iCIMS, Taleo…) and maps its fields.
- Pulls the user's profile + the job-specific tailored resume from your API.
- One-click autofill; **user reviews and clicks submit** (mimics real typing to avoid flags).
- Reports back submission status to the dashboard (`application` event), so the pipeline and extension share one source of truth.

**Hard part (budget for it):** per-ATS field mappings + keeping them current as sites change. Start with the top 5 ATS platforms (covers a large share of postings), expand from there. Let users request new sites.

---

## 9. Notifications

- **Telegram (build first):** create a bot via BotFather, store each user's chat_id, send match/approve messages with inline "Approve / Skip" buttons that call your API webhook. Free, instant, zero approval friction.
- **WhatsApp:** WhatsApp Cloud API (Meta) or Twilio. Requires a business number and **pre-approved message templates** for outbound notifications — plan a few days for approval. Higher friction, do it after Telegram works.

---

## 10. Core data model (starter schema)

```
companies        (id, name, domain, ats_type, ats_slug, last_seen_ok,
                  jobs_last_run, status)   -- the ATS slug registry (§4)
users            (id, email, auth_id, plan, created_at)
profiles         (id, user_id, master_resume, parsed_json, prefs_json,
                  work_auth, locations[], seniority, salary_floor)
jobs             (id, source, external_id, title, company, location,
                  remote, description, url, ats_type, posted_at,
                  raw_json, embedding vector)          -- pgvector
matches          (id, user_id, job_id, score, label, reasons_json,
                  status)   -- discovered|tailored|queued|applied|skipped
documents        (id, match_id, type, storage_url, gdrive_url, version)
applications     (id, match_id, method, status, submitted_at,
                  confirmation, error)  -- method: agent|extension|manual
approvals        (id, match_id, channel, sent_at, decided_at, decision)
events           (id, user_id, type, payload_json, created_at) -- audit/observability
```

---

## 11. Required platforms & accounts (checklist)

**Infrastructure**
- Cloud host — Hetzner / Fly.io / Railway (compute)
- PostgreSQL (managed: Supabase/Neon, or self-hosted) + Redis
- Object storage — Cloudflare R2 or AWS S3
- Docker registry / CI (GitHub Actions)

**Job sourcing (see §4 for the layered plan)**
- Direct ATS APIs — Greenhouse / Lever / Ashby / Workable / SmartRecruiters / Recruitee (free, no account)
- JobSpy (self-host) for LinkedIn/Indeed/Glassdoor/Google/ZipRecruiter
- Remote-tech feeds — Remotive / RemoteOK / Himalayas / Arbeitnow (free)
- Residential proxy provider — IPRoyal / Bright Data / Lightning (for the aggregator layer only)
- (Optional bootstrap) managed jobs/ATS API — Apify ATS actors / JSearch / Coresignal / Adzuna free tiers

**AI**
- LLM API — Anthropic and/or OpenAI (and OpenRouter as a router)
- (Optional) self-hosted embeddings/reranker (no account needed) or OpenAI embeddings

**Browser agent**
- Skyvern (self-host, free) **or** cloud browser (Browserbase/Steel/Hyperbrowser)

**Documents**
- Google Cloud project → Google Drive API (OAuth) for the Drive mirror

**Notifications**
- Telegram BotFather (free)
- Meta WhatsApp Business / Twilio (paid, template approval)

**Extension**
- Chrome Web Store developer account ($5 one-time) — later Edge/Firefox stores

**Ops**
- Sentry, Langfuse (LLM cost/traces), Grafana/Prometheus

**Payments (when monetizing)**
- Stripe

---

## 12. System design & preferred architecture

**Execution model**
- **Per-user pipeline** runs on a schedule (n8n cron → webhook to Core API → enqueue Celery jobs).
- **Fan-out:** scrape → dedup (Redis set on `source:external_id`) → bulk embed → score/rerank → shortlist → tailor → route to apply-fork.
- **Idempotency:** every step keyed by (user_id, job_id) so retries don't double-apply. Applying is a **write with side effects** — guard it hard.
- **Backpressure:** rate-limit scraping and agent-applies per IP/proxy and per user to stay under board thresholds (LinkedIn rate-limits ~10th page per IP → proxies + pacing mandatory).

**Why hybrid, not pure-n8n:** n8n is excellent for scheduling, glue, and notifications, but complex logic in it becomes an unmaintainable canvas (silent failures, unreadable diffs). Keep n8n thin; keep matching/tailoring/agent in versioned Python. Migrate orchestration to **Temporal** when you need durable retries, per-step observability, and multi-tenant scale.

**Scaling path:** single Docker-Compose box (MVP) → separate worker nodes for browser automation (RAM-heavy) → Kubernetes with a dedicated browser pool + autoscaled scrape/tailor workers.

---

## 13. MVP plan (phased)

### Phase 0 — Personal / single-tenant proof (1–2 weeks)
Prove the loop end-to-end for **one** user, no UI polish.
- Seed ~200–500 known tech companies' ATS slugs (by hand + a public list); pull Greenhouse/Lever/Ashby directly on a cron → Postgres. Add JobSpy (1–2 boards) + one remote feed for breadth.
- Embedding + cosine score (skip cross-encoder), threshold filter.
- LLM tailors resume + cover letter (with the zero-fabrication validator) → PDF to R2.
- **No autonomous apply yet.** Telegram message with the tailored docs + job link for manual apply.
- Orchestrated in n8n + a couple of Python scripts.
**Goal:** confirm match quality and doc quality are actually good. If matching is weak, nothing else matters.

### Phase 1 — MVP product (3–6 weeks)
- Next.js dashboard: profiles, discovered jobs with scores, generated docs, status.
- Auth (Clerk/Supabase), multi-user, Postgres+pgvector, Redis+Celery, FastAPI.
- Add cross-encoder reranking + explainable "Good Fit" labels + work-auth filter.
- Grow the ATS company-slug registry automatically via the reverse-index (resolve every scraped company → detect ATS → store slug) + change-detection per board.
- **Plasmo extension v1:** autofill for the top 3–5 ATS + "submit yourself" flow.
- Telegram approve/skip buttons wired to the API.
- Google Drive mirror of documents.
**This is a shippable Jobright-style copilot** (match + tailor + assisted autofill).

### Phase 2 — Autonomous apply (the risky differentiator) (4–8 weeks)
- Skyvern/browser-use worker for **safe portals only**, behind the validator gate.
- Cloud browser + residential proxies; per-ATS action caching for cost.
- Human-in-the-loop for everything else; WhatsApp channel added.
- Observability: agent success rate, per-apply token cost, failure replay.

### Phase 3 — Scale & moat
- Broaden board coverage + ATS coverage; dedup/enrichment quality.
- Insider-connections/referrals layer (Jobright's 4× interview claim lives here).
- Temporal migration; K8s browser pool; cost controls; anti-abuse.

**Build order rule:** matching quality → document quality → assisted autofill → autonomous apply. Never build the autonomous agent before the assisted path works, because the agent depends on the same field-mapping and validation you build for the extension.

---

## 14. Ballpark running costs (early stage, order-of-magnitude)

- Compute (Hetzner CX32 + workers): ~$20–60/mo
- Postgres + Redis (managed small): ~$20–40/mo
- Residential proxies: **the swing factor** — from ~$50/mo light to hundreds at volume (2–5× infra)
- LLM: scoring is cheap per job; tailoring is the cost — budget per-run and cache aggressively. Roughly $0.10–0.50 per full tailored application (matches Jobright-template economics).
- Cloud browser (if used): ~$0.10/compute-hr → pennies per apply, but adds up at volume.
- Telegram: free. WhatsApp: per-conversation pricing.

**Cost discipline:** hard-filter before embeddings, embed before LLM, LLM only on the shortlist, cache tailored docs and per-ATS agent plans. This ordering is what keeps it "cheap and light."

---

## 15. Key risks & mitigations (you've accepted these — here's how to contain them)

| Risk | Mitigation |
|---|---|
| **Account bans** (LinkedIn/ATS auto-submit) | Never server-side auto-submit on risky portals; use the extension (user's session) or approval hand-off. |
| **Anti-bot / scraper breakage** | Residential proxies, pacing, headed browsers; expect selectors to rot — monitor block rates, keep JobSpy updated. |
| **Resume hallucination** | Evidence-vault prompting + traceability validation; strip unbacked claims. This is the #1 quality/legal risk. |
| **Agent mis-submits** (side effects) | Validator gate + screenshot before submit; idempotency keys; low-stakes portals first. |
| **n8n complexity ceiling** | Keep n8n thin; heavy logic in versioned code; migrate to Temporal at scale. |
| **Cost blowups** | Filter-before-spend ordering; caching; per-user rate caps; Langfuse cost tracking. |
| **Legal/ToS** | Understand each board's terms; prefer official APIs where they exist; treat scraping/auto-apply as the accepted-risk surface and isolate it. |

---

## 16. Open-source projects worth forking or studying

- **Direct ATS APIs** — Greenhouse/Lever/Ashby/Workable public JSON (primary tech source, §4); study any OSS "ATS auto-detect" scraper to replicate slug detection + change-detection.
- **JobSpy** — multi-board aggregator scraper (breadth layer, near drop-in).
- **Free remote feeds** — Remotive / RemoteOK / Himalayas / Arbeitnow JSON APIs.
- **Resume-Matcher** — ATS + semantic match and JD-aware rewrite.
- **ResumeLM** — Next.js AI resume builder (dashboard + tailoring reference).
- **RenderCV** — deterministic YAML→PDF resume generation.
- **Reactive Resume / OpenResume** — builders/parsers, self-hostable.
- **Skyvern**, **browser-use**, **Stagehand** — browser-agent apply engines.
- **Plasmo** — the extension framework.
- **n8n** — orchestration; **Temporal / Windmill** for the code-first upgrade.
- GitHub topics to mine: `job-scraper`, `job-matching`, `resume-tailoring`, `resume-matching` — several repos already combine JobSpy + pgvector matching + tailoring + a Chrome extension + human-approved auto-apply, i.e. near-exact references for this whole system.

---

*End of blueprint.*
