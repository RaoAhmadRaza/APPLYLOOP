# V0 demo plan — dashboard + extension

> **Goal:** a working end-to-end demo by the weekend of 2026-08-15. Someone watching should
> see a résumé go in, jobs come back scored with reasons, a tailored résumé and cover letter
> get written, and a real job form fill itself in the browser.
>
> **Owner's scope call, 2026-08-10:** build M8 + M9 plus thin slices of M6 and M7 now; run
> M5's gate **last**. This is deliberately out of the build order in CLAUDE.md §9.1 — see
> *Risks accepted* below, which states exactly what that costs and what must not happen
> before it is paid back.

---

## 1. What already exists

**Every feature on the demo list already works, headlessly.** The gap is two front ends.

| Capability | State | Where |
|---|---|---|
| Job fetching | ✅ 6 ATS providers + 8 free feeds, unattended on a schedule | M1, M2 |
| Cross-source dedupe | ✅ one row survives, keeps the ATS apply URL | M2 |
| Résumé parsing | ✅ PDF/DOCX/TXT → structured profile + evidence vault | M3 |
| Profile & prefs | ✅ full CRUD, round-trips through the API | M3 |
| Scoring | ✅ every match carries a score **and** a human-readable reason | M4 |
| Tailored résumé + letter | ✅ real PDFs in MinIO and Google Drive | M5 |
| REST API | ✅ CRUD on all 8 tables + events | M0 |
| Task triggering | ✅ `api.queue.enqueue(name, *args)` fires any Celery task | M0 |

`apps/web` and `apps/extension` are **empty directories**. Both are greenfield; nothing has
to be untangled first.

---

## 2. The one real backend gap

**List endpoints support `limit` and `offset` and nothing else** (`apps/api/src/api/crud.py`
`read_many`). No filtering, no sorting, no joins. And `matches` carries `job_id`, not the
job's title or company.

So a naive dashboard asking "show me this user's matches, best first" would fetch every
match and then fetch each job one at a time. At demo scale that is ~78 round trips for one
screen.

**Fix it once, with one purpose-built read model.** Everything else the dashboard needs is
already served by the generic CRUD. This is the highest-value hour in the plan.

---

## 3. Scope

**In**

- one purpose-built pipeline read endpoint (§2)
- approve / skip as explicit state transitions (slice of M6 — **no Telegram**)
- a "tailor this match now" trigger (slice of M7 — **no new schedule**)
- a document link the browser can open
- dashboard: 5 screens (§6)
- extension: Greenhouse autofill, **fill only, no submit** (§7)

**Out, and each for a stated reason**

| Out | Why |
|---|---|
| Telegram notifications | Free and ~a day, but invisible in a screen-share demo. M6 proper, after. |
| Auth (Clerk/Supabase) | Demo runs on localhost. **See Risks — this is the one that must not slip.** |
| Extension submit | Irreversible. §3.4 wants idempotency keys and a validator gate; M10's gate is a *negative* test. Filling a real form correctly is the demo beat; clicking submit adds risk and no narrative. |
| Lever / Ashby field maps | Greenhouse first. Add if Thursday is calm. |
| Proven unattended run | M7 proper. The beat already runs ingest/dedupe/match; tailoring stays manual by design. |
| M5 gate | Last, per the owner. |

---

## 4. The demo narrative

The screens exist to serve this sequence. If a screen does not appear here, it is not in V0.

```
1. "Here's a real résumé."        upload → parsed profile appears, skills and all
2. "It found these jobs."          scored list, best first, real companies
3. "Why this one?"                 met / missing / disqualifiers, quoted from the posting
4. "It wrote these."               tailored résumé + cover letter, openable PDFs
5. "I approve it."                 one click, status moves
6. "Now watch."                    extension fills a real Greenhouse form, field by field
7. "And it never invents."         a HOLD: a required field with no data, left blank
```

Beat 7 is the differentiator. Every competitor demo shows autofill; showing the agent
*refusing* to guess is what makes the safety argument visible.

---

## 4b. Score and keyword quality (do first — cheap, and it lifts every screen)

> **BUILT AND MEASURED 2026-08-10. Read this box before the four sections below, which
> are kept as written so the corrections are legible against them.**
>
> **None of 4b.1–4b.4 can move a match score.** The matching stage's only profile input is
> `profiles.master_resume` — raw text — and the score is coverage of the *posting's* stated
> requirements against it (`match.py:74`, `score.py:23-71`). `evidence` and
> `parsed_json.skills` are M5's inputs, not M4's. These are document- and keyword-quality
> fixes and were worth building as such.
>
> **4b.2 shipped one-directional.** A symmetric alias fails committed case `S-06`.
> **4b.3 shipped narrowed** to the posting's company and title; the literal version is
> class F2. **4b.4 shipped and did not work** — nine live pairs, 0 letters written, and
> the cause was never vocabulary. Full accounting in `evals/fabrication/BAR.md` §8 and
> `docs/DECISIONS.md`.
>
> Landed alongside, neither of them planned: the `met=[]` degenerate zero is re-asked
> rather than re-weighted, and an empty parse no longer wipes the vault.

### 4b.1 The parser drops skills — bug, highest value

M3 sometimes stores `Languages: Python, Go, SQL, TypeScript` as **one** evidence claim
instead of four. ~~Those skills then don't count toward coverage at all, so the score is
suppressed by a parsing defect rather than by the candidate.~~ **Wrong — the matcher never
reads the vault.** What it actually costs: one chip reading `Languages: Python, Go, SQL,
TypeScript` on demo beat 1 instead of four, and it is the same defect that blocked M5's
first real document by calling nine of the candidate's own skills fabrications.

*Fix:* split group lines on their own delimiters at parse time, never by substring.
`unsplit_skills` already exists as a vault fixture, so the failing shape is on hand.

*verify:* re-parse `senior_backend.pdf`, assert 4 skill claims not 1; re-score one match and
record the score before/after.

### 4b.2 Skill alias map

The vault says `Postgres`, the posting says `PostgreSQL`, and an ATS keyword-matches on
exact strings. Today the prompt says *"do not translate one vendor's name to another's"* and
`S-06` exists to enforce it — so we lose the keyword on a skill the candidate genuinely has.

*Fix:* a small static alias table (`Postgres ≡ PostgreSQL`, `K8s ≡ Kubernetes`,
`JS ≡ JavaScript`, `GCP ≡ Google Cloud`). An alias is only usable when the candidate already
holds the skill — this changes **spelling**, never possession.

*verify:* a bullet citing a `Postgres` claim may render `PostgreSQL`; a bullet citing nothing
may not render either. `S-06`'s substring rule stays — `Postgres` must still not license
`PostgresQL Administration Certified`.

### 4b.3 Bullets may use the posting's vocabulary for facts already held

Rule 4 currently forbids taking *any* word from the posting unless the cited evidence
contains it. That was written against fabrication, but it also blocks honest rephrasing
toward the language the ATS scans for.

*Fix:* allow posting vocabulary for **phrasing**, keep the hard rules on facts — no number,
employer, technology, scope or seniority that is not in the cited claim.

*verify:* `test_fabrication_guard` stays at 17/17 offline. If it moves, this is wrong.

### 4b.4 Let the cover letter actually be written

**100% of letters currently fail to generate.** Every word of every paragraph must appear in
the cited evidence or in an 18-word connective list (the plan said 20), which is not enough
English to write a sentence with.

*Fix:* widen the connective vocabulary. **Numbers and metrics stay hard** — the two real
catches on `gpt-5` were `the number 13` and `the number 17`, figures the résumé never
stated, and that rule does not move.

**This reverses a decision recorded in BAR.md §8**, which refused to widen `words.py` on the
grounds that it was fitting-to-result while chasing a green gate. The reason is different
now: a letter stage that produces nothing on every honest pair is a product failure, and the
owner has ruled prose quality in scope. Log it in §8 as an owner decision, and re-measure —
the letter rate is reported, not gated, so this cannot make the gate lie.

*verify:* letters generate on a majority of honest pairs; `test_fabrication_guard` unmoved;
the F4 number cases still blocked.

> **Measured 2026-08-10: 0 of 9 live pairs wrote a letter.** The guard is unmoved and the
> F4 cases are still blocked, so the fix is not harmful — it just is not the fix. Round 1
> blocked 3/5 on invented years-of-experience numbers (F4, working); a prompt rule
> forbidding digits moved that to 1/4 and the failures relocated to content nouns —
> `infrastructure`, `backend`, `accountabilities` — which cannot be added, because a
> paragraph's nouns are its claims. **Widening the list further is not the route.** For
> the demo, screen 3 already specifies the honest empty state; a letter needs either a
> stronger model or paragraph-level dropping, and both are decisions for after the demo.

**Order:** 4b.1 first — it is a bug, it is cheap, and it may be worth several points on its
own since skills are currently being silently lost.

---

## 5. Backend work (do first — the dashboard depends on it)

### 5.1 Pipeline read endpoint

`GET /users/{user_id}/pipeline?status=&limit=&offset=`

One query joining `matches` → `jobs` → `documents`. Returns per row:

```
match_id, score, label, status, reasons_json
job: title, company, location, remote_mode, url, ats_type, posted_at
documents: [{type, gdrive_url, version}]
```

Ordered by `score DESC, created_at DESC`. **Reads only the deduped pool**
(`closed_at IS NULL AND canonical_id IS NULL`, §6.3).

*verify:* seed 3 matches with known scores, assert order and that a closed/duplicate job
never appears.

### 5.2 Approve / skip as transitions

`POST /matches/{id}/approve` · `POST /matches/{id}/skip`

Not raw `PATCH status=`. §6.1's state machine says nothing may skip a transition, and a
dedicated endpoint is where that rule can live. Approve is legal only from `tailored`.
**Idempotent** — a double click is a no-op returning the same row (§3.4: double-taps are
expected, not exceptional).

*verify:* approve twice → one transition, one row, second call not an error. Approve from
`discovered` → 409.

### 5.3 Tailor trigger

`POST /matches/{id}/tailor` → `enqueue("workers.tasks.tailoring.tailor_match", match_id)`

Returns 202. The stage already no-ops unless the match is `discovered`, so this cannot
double-spend.

*verify:* call it on a `tailored` match → no second document row.

### 5.4 Document link

`GET /documents/{id}/download` → 302 to a short-lived signed URL from `packages/storage`.

`gdrive_url` works today and is the fallback if signing takes longer than an hour, but it
depends on Drive being configured and is a public-ish link. The signed URL is the honest one.

*verify:* the URL opens the right PDF and expires.

---

## 6. Screens

Design notes for each: **what it is for, what it shows, where the data comes from, what
states it must handle, and which demo beat it serves.** Empty and error states are specified
because a demo hits them — an unparsed résumé, a match with no documents yet.

Shared shell: left nav (Profile · Jobs · Applications), user switcher (there is no auth —
a plain dropdown of `GET /users`), and a global "last pipeline run" timestamp from
`GET /events?type=match.scored`.

---

### Screen 1 — Profile & résumé · *beat 1*

**Purpose:** prove the system reads a real résumé correctly.

**Data:** `GET /profiles/{id}` → `master_resume`, `parsed_json`, `prefs_json`, `work_auth`,
`locations[]`, `seniority`, `salary_floor`. Upload: `POST /profiles/{id}/resume`
(multipart). Evidence count: `GET /evidence?profile_id=` *(note: `evidence` has no CRUD
router today — either add one or show the count from `parsed_json`)*.

**Layout, three regions**

1. **Upload** — drop zone, accepted types PDF/DOCX/TXT. After upload the parse is async;
   poll the profile until `parsed_json` is non-empty.
2. **Parsed result** — the proof. Name, contact, then per role: employer, title, dates,
   bullets. Then a skills chip row. Then education and certifications.
3. **Preferences** — editable: target titles, locations, remote modes, salary floor,
   work auth, must-have / exclude keywords. Saves via `PATCH /profiles/{id}`.

**States**
- *empty* — no résumé yet: upload zone only, everything else hidden
- *parsing* — spinner with "reading your résumé"; takes ~10–20s on a real model
- *parsed* — the full view
- *parse failed* — the profile keeps its old `parsed_json`; show the error from
  `GET /events?type=profile.parse_skipped`, and keep the previous parse visible

**Demo note:** the skills chips are the moment the audience believes it actually read the
document. Make them prominent.

---

### Screen 2 — Job matches · *beat 2*

**Purpose:** show volume and ranking at a glance.

**Data:** `GET /users/{id}/pipeline` (§5.1).

**Layout:** a table or card list, best score first. Per row:

```
score (0-100, prominent)   job title   company   location + remote mode
one-line reason            reasons_json.summary, truncated
status pill                discovered · tailored · queued · approved · applied · skipped
action                     "Tailor" if discovered · "View" if tailored
```

**Controls:** status filter (pills), and a count — *"36 of 952 candidates cleared the
filters"*. That number is the cost story in one line.

**States**
- *empty* — "no matches yet, the matcher runs every 12h" + a manual trigger button
- *loading* — skeleton rows
- *scored but below threshold* — visible under the `skipped` filter, greyed. **Do show
  these** — that the system records what it rejected is a feature, not clutter.

**Demo note:** sort is score-descending and the top row should be a genuinely good match.
Check this before the demo; if the top row is weak, that is the whole first impression.

---

### Screen 3 — Match detail · *beats 3, 4, 5*

**Purpose:** the heart of the demo. Why this job, what was written, approve.

**Data:** `GET /matches/{id}`, `GET /jobs/{job_id}`, documents from the pipeline endpoint.

**Layout, two columns**

*Left — the posting.* Title, company, location, posted date, a link to the real apply URL,
and the description (collapsed past ~15 lines).

*Right — the verdict.* This is the part worth designing carefully:

```
Score            big number + the threshold it cleared ("36 / threshold 20")
Summary          reasons_json.summary
✅ Met           reasons_json.met[]         — verbatim spans FROM THE POSTING
❌ Missing       reasons_json.missing[]     — same
🚫 Disqualifiers reasons_json.disqualifiers[] — red, the posting's own words
⛔ Bars          reasons_json.bars[]        — sentences THIS SYSTEM wrote, styled
                                              differently from quoted spans
Coverage         reasons_json.coverage as a ratio
Similarity       reasons_json.similarity
Provenance       reasons_json.model + embed_model, small, footnote
```

**`met`/`missing`/`disqualifiers` are quoted from the job description; `bars` are our own
sentences.** Style them differently — the distinction is real and the live gate asserts it.

*Below — documents.* Résumé and cover letter cards: type, version, "Open PDF"
(`GET /documents/{id}/download`), and Drive link if present. **A cover letter may legitimately
be absent** — on the current model most are. Show "not written — could not be grounded in the
vault", not an error.

*Footer — actions.* `Approve` (primary), `Skip` (secondary), `Tailor now` when `discovered`.
Approve is only enabled from `tailored`.

**States:** discovered (no docs, Tailor button) · tailoring (spinner, ~50s) · tailored ·
blocked (validator refused — show the reason, it is a *feature*) · approved · skipped.

**Demo note:** the "blocked" state is worth having a real example of. A document the system
refused to ship is the most convincing screen in the product.

---

### Screen 4 — Document viewer · *beat 4*

**Purpose:** the PDF is real and ATS-readable.

Inline PDF (`<iframe>`/`<embed>` on the signed URL) with Download and Drive buttons, plus
version and generated-at. Side panel: `bullets_kept`, `bullets_stripped`, `skills_kept`,
`fabricated_skills` from the `tailor.generated` event.

**Demo note:** *"zero fabricated skills, two bullets stripped because they could not be
traced"* — say the numbers out loud. That is the product.

Can be a modal over Screen 3 rather than a route.

---

### Screen 5 — Applications · *beat 6 aftermath*

**Purpose:** close the loop — the extension wrote something back.

**Data:** `GET /applications` joined to matches/jobs.

Per row: company, title, method (`extension` · `agent` · `manual`), status, submitted_at,
confirmation, error. Empty state: "nothing submitted yet".

**Demo note:** after the extension fills the form, this screen gains a row. That is what
makes the extension feel connected rather than a separate toy.

---

### Optional — Pipeline home

Only if the rest is done early. The funnel as numbers, which is the cost architecture made
visible:

```
25,801 open  →  952 after filters  →  40 shortlisted  →  36 above threshold  →  2 tailored
```

Plus per-source row counts (`GET /events?type=ingest.run`). Nice, not required.

---

## 7. Extension (M9)

**Plasmo, MV3.** Chrome only for the demo.

**Scope: fill, verify, report. Never submit.**

**Popup**
- lists approved matches (`GET /users/{id}/pipeline?status=approved`)
- each row: company, title, "Fill this form"
- detects whether the current tab matches the job's apply URL host
- empty state: "approve a match in the dashboard first"

**Content script — Greenhouse only**
1. Fingerprint the page (host + form structure) → confirm it is a Greenhouse form
2. Resolve fields from a **static field map** — name, email, phone, LinkedIn, résumé upload,
   plus common free-text questions
3. Fill only from the profile and the tailored documents
4. **HOLD anything unmapped or required-with-no-data.** Never invent a value. Highlight
   filled fields green and holds amber
5. Read every field back from the DOM and compare with intent — the verification step, and
   the thing M10 will reuse
6. Report: `POST /applications` with `method=extension`, `status=filled`, the HOLD list in
   `error` (or a dedicated column later)

**Explicitly not doing:** clicking submit, solving CAPTCHAs, evading bot detection, touching
a portal that needs the user's own login (§3.2 — non-negotiable, and the reason the extension
exists at all).

*verify:* on a **real** Greenhouse posting, every mapped field holds the intended value read
back from the DOM, and at least one unmapped required field is reported as a HOLD rather than
guessed.

**Biggest risk in the plan.** Real DOM, real variation between Greenhouse tenants. Budget a
full day and pick the target posting **early** — ideally one already in the pool with an
`ats_type = greenhouse` and a working apply URL.

---

## 8. Order and verification

```
Day 1a  quality fixes §4b.1-4b.4        → score before/after on one match; letters generate
Day 1b  backend §5.1-5.4                → pytest: order, idempotency, 409, no double-spend
Day 2   dashboard shell + screens 1, 2  → upload a real résumé, see it parsed and scored
Day 3   screens 3, 4, 5                 → approve a real match end to end
Day 4   extension: popup + Greenhouse   → fill a real posting, HOLD proven
Day 5   rehearsal on real data, fixes   → run the whole narrative twice, unassisted
Last    make verify-live-tailor         → M5's gate, ~13 min, ~$2
```

§4b goes first because it changes the numbers every screen displays. Doing it after the
dashboard means demoing scores, then changing them.

Each day ends with the demo narrative runnable up to that point. If a day slips, the demo
degrades gracefully instead of collapsing — that is the reason for this order and not a
prettier one.

---

## 9. Risks accepted, explicitly

**1. Documents are unverified on the model that writes them.** M5's gate is red on
`deepseek-v4-flash` — on *coverage*, with **0 escapes**, and the prompt defect behind it is
fixed but not re-measured. The offline `test_fabrication_guard` is green and the validator is
unchanged, so the mechanism is intact; what is unmeasured is the new model against it.
Acceptable for a demo to humans. **Not acceptable before a document reaches a real employer.**

**2. No auth. At all.** Every CRUD route is open and `POST /profiles/{id}/resume` accepts an
upload for any profile id. **The demo must run on localhost or a private tunnel.** If it goes
on a public URL, that is a data-exposure incident, not a shortcut. Decide this now, not on
Friday night.

**3. Out of build order.** CLAUDE.md §3.6 orders things so later stages reuse earlier work.
The extension's field maps are the part M10 reuses, so that ordering survives. What is
genuinely deferred: Telegram (M6), a proven unattended run (M7), and M5's gate.

**4. Demo data is one user and one pool.** Multi-tenancy is untested (Part 14, open). Do not
demo two users switching.

**5. The extension writes to `applications`.** That table's unique constraint on
`(match_id, method)` is the duplicate guard (§3.4). Fill twice on the same match → the
database refuses the second. Handle the 409 in the extension rather than checking first.

---

## 10. After the demo

In order: M5's gate green → M6 proper (Telegram) → M7 (proven unattended run) → auth →
M9's remaining ATS field maps → M10.

Carried over and unrelated to the demo: proxy credentials (M2's last clause), R2 credentials,
the `google` aggregator returning 0 rows, growing M4's golden set past ±0.05, and the fuzzy
dedupe trigger that has already fired (101 copies of one posting).
