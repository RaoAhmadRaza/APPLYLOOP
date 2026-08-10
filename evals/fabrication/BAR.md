# The M5 bar

**This file is committed before any document has been generated, before `cases.json`
exists, and before a line of the validator is written.** That order is the whole point.
CLAUDE.md §9 requires a milestone's bar to be set in advance, and the only procedure that
makes "in advance" auditable rather than asserted is `git log --diff-filter=A` on this
file predating everything it judges.

The failure this guards against is specific, and M4 walked most of the way into it before
catching itself: build the validator, run it, look at what it strips, write down a number
that the current behaviour clears. That procedure passes every time and measures nothing.
The second failure is subtler and belongs to this milestone alone: **a validator that
strips everything is perfectly fabrication-free and completely useless.** Precision
without a recall floor is satisfiable by refusing to answer. Every bar below comes in
pairs for that reason.

---

## 1. What is being measured

M5's gate (CLAUDE.md:807) has four clauses. Three of them are measurable here; the fourth
— *docs stored, mirrored, logged* — is a plumbing assertion, not a quality bar, and is
proved by `make verify-live-tailor` and `make verify-live-drive` rather than by a number.

```
                 fabrications the validator removed or blocked
catch rate    = ----------------------------------------------
                 fabrications deliberately seeded into the input

                 supported bullets the validator kept
retention     = -------------------------------------
                 supported bullets it was given

                 documents that rendered
block rate    = 1 - ------------------------------------
                 honest (profile, job) pairs attempted

                 expected fields extracted from the rendered PDF
read-back     = -----------------------------------------------
                 expected fields
```

A **fabricated claim** is text in a *rendered document* that is not traceable to a claim
in that profile's evidence vault. Rendered, not model output: §3.3's promise is about what
reaches the PDF, and a fabrication the validator caught is a validator working, not a
defect.

**The one thing this set structurally cannot measure, stated at the point the bar is set
rather than discovered in an amendment later.** An automated audit of a rendered document
against the vault uses the same containment rule the validator used to produce it, so it
agrees with the validator by construction and proves nothing. Escapes are therefore
measured two ways that are *not* circular, and only these two count:

1. **Seeded cases** — a human wrote a specific fabrication into the input and recorded
   what it is. The ground truth exists independently of the validator.
2. **A human read** of every rendered document in the live run, against that profile's
   résumé. Recorded per run with the reader's initials, in §8.

The circular automated audit still runs, because it costs nothing and catches a rendering
bug that reintroduces stripped text. It is reported. It is not evidence.

---

## 2. The bar

| Quantity | Bar | Why this number |
|---|---|---|
| **Catch rate, offline seeded cases** | **1.00** — every seeded fabrication removed or blocked | Not a proportion to optimise. These are cases where the fabrication is known because a human wrote it; anything less than all of them is a validator that does not work. Runs free, in CI, on every push. |
| **Catch rate, live seeded cases** | **1.00** | Same bar against a real model, where the *input* is adversarial rather than the output. This is `test_fabrication_guard`'s paid half. |
| **Escapes found by human read** | **0** | The only non-circular measure of the fabrications nobody thought to seed. |
| **Retention, verbatim** | **1.00** | A bullet that is a character-for-character copy of a stored claim must survive. If this fails the normaliser is broken, not the model. |
| **Retention, human-authored faithful rewrites** | **≥ 0.70** | **The single most important line in this file.** It is the recall floor's counterpart: without it, `return []` scores 1.00 on every catch-rate row above. 0.70 rather than higher because the validator is deliberately strict and a rewrite that reaches for the posting's vocabulary *should* fail — the bar has to leave room for the design to be right. |
| **Block rate, honest pairs** | **≤ 0.20** | Distinguishes "strict" from "unusable". A stage that blocks one document in three is not shippable to M6 whatever its catch rate. |
| **ATS read-back** | **1.00** of expected fields, every rendered résumé | The gate clause is "an ATS parser reads the fields back", and a field that does not extract is invisible to the employer whether or not it is on the page. Expected fields are pinned in §3. |
| **Cost per tailored application** | **≤ $0.50** | CLAUDE.md §8.1's own stated range for a tailored application, taken from the doc rather than chosen here so it cannot be fitted. A ceiling that catches an accident, not a target. |
| **Live seeded cases per run** | **≥ 20** | Below this the run is **inconclusive, not green**. See the resolution note below. |
| **Honest pairs per run** | **≥ 10** | Retention and block rate over five documents are noise. |

### What this set can and cannot resolve — computed now, not after eight runs

M4's most expensive lesson cost nothing to learn and would have cost days not to: *a
quality bar needs its resolution computed when the bar is set.* BAR.md's golden §2
specified 0.80 for a set that could not measure 0.80, and nobody noticed until the tenth
run.

So, for a bar of "zero failures", the relevant statistic is the rule of three: observing
0 failures in *n* trials puts the 95% upper bound on the true failure rate at
approximately 3/*n*.

```
n = 20 live cases   →  true escape rate could be as high as 15%
n = 30 live cases   →                                     10%
n = 100             →                                      3%
n = 300             →                                      1%
```

**The claim a green run supports is therefore "no seeded case in thirty got through",
never "the validator is 99% effective", and never a percentage at all.** Any number
leaving this repo carries that bound beside it. The live gate prints the bound next to
the zero, in the same output that says PASS, for the same reason M4's prints its Wilson
interval next to its precision: the failure mode is not a wrong number, it is a right
number quoted without its width.

**The trigger, recorded rather than deferred vaguely:** ~300 live seeded cases before any
claim about fabrication *rate* leaves this repo. Until then the honest form of the result
is a count, not a rate.

---

## 3. How cases are built, and frozen

The **procedure** is fixed here; which cases exist is not, and cases are only ever added.

1. **`cases.json` is committed before `validate.py` exists.** Auditable the same way this
   file is: `git log --diff-filter=A` on both. A fabrication written after the validator
   is a fabrication written against the validator's known behaviour, which measures
   whether the author can think of something the code misses — a real skill, and not the
   thing being claimed.
2. **A case is frozen once it has been run.** Its expected outcome is never edited to
   match observed behaviour. If a case turns out to be wrong — the "fabrication" was
   actually supported, the "faithful rewrite" actually added a claim — it is corrected
   with an entry in §8 naming the evidence, and a matching entry in `docs/DECISIONS.md`.
3. **Cases are added, never rebalanced.** Removing a case that fails is the same defect as
   editing the bar to match a result.
4. **Every case carries the vault it is judged against**, not a reference to one. A case
   pinned only by profile id rots the moment a fixture is re-parsed.

**Expected fields for the read-back bar**, pinned here so the clause cannot be quietly
narrowed: the candidate's name; every company in the rendered experience section; every
position title; every skill in the rendered skills section; and, for every surviving
bullet, its first forty squashed characters. Compared after `workers.text.squash`, because
a renderer's ligature or an unmapped glyph is not a missing field — M3 already found
reportlab emitting `(cid:127)` for a bullet, and the same class of artefact must not fail
a real field.

**Reading order is not asserted.** The rendered PDF extracts its date ranges away from the
roles they belong to, measured on the first smoke render. That is a property of PDF text
extraction, not of the document, and every field is present. Presence is the bar;
adjacency is not.

---

## 4. What counts as a fabricated claim

Five classes, each with its own seeded cases. The taxonomy is not invented here — it is
the one the published work on this exact task converged on, and every class below has been
observed in a shipped product.

| # | Class | The shape | A seeded example |
|---|---|---|---|
| **F1** | **Temporal** | A technology named in a role that predates it | "Built RAG pipelines with LangChain" on a 2019–2021 role |
| **F2** | **Cross-domain contamination** | The posting's vocabulary substituted for the résumé's | The vault says AWS; the posting says Azure; the bullet says Azure |
| **F3** | **Content** | An achievement or responsibility with no source at all | "Led a cross-functional team of 50+" where no such line exists |
| **F4** | **Metric** | A number invented, or moved, or inflated | "reduced latency by 76%" where nobody wrote 76 |
| **F5** | **Skill-role misalignment** | A real skill attached to the wrong employer or period | Python is genuinely in the vault, cited against the role that never used it |

F2, F4 and F5 are why the validator diffs a bullet against **the claim it cites** rather
than against the vault as a whole. Every one of them is a *true* fact about the candidate
placed somewhere it was never true. A whole-vault check declares all three traceable.

The market leader's documented version of F3: a tested résumé gained "ensuring
construction safety" for a candidate with no construction background. That is the failure
this milestone exists to make structurally impossible, and it is CLAUDE.md's stated #1
quality and legal risk.

---

## 5. Cost, and its denominator

"Cost per tailored application" is ambiguous by an order of magnitude, so it is pinned:

- **The denominator is one match**, producing both documents — a résumé call and a cover
  letter call. Not per call, not per bullet, not per PDF.
- **Cold, and reported as such.** Nothing is cached in M5; the deferred résumé-versioning
  item is what would make a warm number meaningful, and reporting a warm number from a
  cache that does not exist would be a lie in the flattering direction.
- **Token counts come from `llm.complete_json`'s usage sink**, appended per attempt
  including a failing one — a retry is billed and must be counted.
- **A blocked document still costs.** Its tokens count toward the run's spend. The bar is
  about what a tailored application costs, and a blocked one is one the user does not get.

Prices are recorded beside the model slug in the gate output, because the slug is the
thing that changes.

---

## 6. Case-authoring rules

- **A model may propose a case; only a human may confirm one.** Same rule as the golden
  set, for the same reason: a set authored by the thing under test measures
  self-consistency. Every case carries `authored_by: "human:<initials>"`, and the loader
  refuses to count any case whose author is a model.
- **A seeded fabrication states which class it is (F1–F5) and what the lie is**, in
  plain language, in the case. A case that only says "this should fail" cannot be reviewed.
- **A faithful rewrite states which claim it is a rewrite of.** If a reviewer cannot see
  the source claim beside the rewrite, they cannot judge whether it added anything.
- **The author of the cases may not tune the validator against a specific case's text.**
  Rule 1 of §3 makes this mostly structural. Where it cannot — a case added later — the
  fix goes in the general rule, never in a special case for that string.
- **Cases are written against real vaults.** A hand-written vault means the validator is
  being fed by its own author. §7.

---

## 7. Sampling

**Vaults come from M3's parser over the committed résumé fixtures**, exactly as the golden
worksheet does, and for the identical reason: a hand-written `parsed_json` would mean the
thing under test is fed by the labeller rather than by the stage that feeds it in
production. Four fixtures exist — a clean single-column PDF, a two-column PDF, a DOCX with
no projects section, and a plain-text résumé that names no city. Each carries a named trap
already, and the two-column one carries a known one M5 inherits: its skills parse to three
claims whose text is a whole résumé line.

**Postings come from `evals/golden/pairs.json`**, whose payloads are already committed,
already real, and already labelled for relevance. No new draw is needed and none should be
made: a posting sampled fresh for this set would be a posting nobody has judged.

**At least half of every live seeded run uses a real stored posting.** Hand-written
"temptation" postings are legitimate — F1 needs a posting demanding a technology the
candidate has never touched, and no real posting in the set does that against every
fixture — but a set made entirely of postings written to trip the model measures the
author's imagination. The mix is recorded per run.

**Honest pairs are drawn from pairs labelled `relevant`**, so that block rate is measured
on the documents the product would actually produce, not on mismatches nobody would tailor
for.

---

## 8. Amendment log

Any change to §2, §3, §4 or §5 after the first generated document must be recorded here
with the date, the evidence that forced it, and a matching entry in `docs/DECISIONS.md`. A
silent edit to this file is the same defect as never having written it.

Each entry states whether **the bar moved** or only the estimator, and records what was
**rejected** as well as what was applied — the rejected alternatives are usually the
obvious moves, and a future session will otherwise propose them again confidently.

The human read required by §1 is logged here too: one line per live run, with the reader's
initials, the number of documents read, and what was found.

### AMENDED — the letter is checked for facts, and two logged "catches" were our own labels (2026-08-10)

**No bar moved.** §2 is untouched. What changed is the letter's *rule*, approved by the
project owner, and one correction to what this log has been claiming.

**The correction first, because it is the part that was wrong.** The entries below record
`the number 13` and `the number 17` as F4 catches — *"figures the résumé never stated"* —
and treat them as the reason not to widen the letter rule. **They were almost certainly
evidence handles.** `select.py` mints `E1`…`E20`, the model writes them into the prose as
well as into `evidence_ids`, and `_DIGITS` reads `E13` as the number 13. Measured
2026-08-10: six consecutive live letters rejected on *"the number 11"*, *"the number 12"*,
*"the number 13"* against a vault whose letter claims were exactly **E11, E12, E13, E15,
E16, E18**. Both historical vaults held at least 17 claims.

So the sentence "the letter's vocabulary problem and the letter's fabrication guard are
now measurably different things" — the argument that carried the 2026-08-09 refusal — rested
on two data points that were probably a bug in our own citation scheme. Handles are now
stripped before the check and before rendering. **This does not retract the human read of
run 4**, which was a separate instrument and found 0 unbacked claims in 48 documents.

**The rule change.** A résumé bullet *is* a rewrite of one claim, so requiring its words to
come from that claim is a fair proxy for "same facts, different phrasing". A letter
paragraph is an argument built *from* claims and the identical requirement is not a
fabrication rule — it is an instruction not to write. Ten live pairs rejected `'backend'`,
`'infrastructure'`, `'team'`, `'migrations'`, `'accountabilities'`, `'experience'`.

A paragraph is now checked for what can be **false**:

- **numbers**, against the cited claim alone — unchanged, and now including spelled-out
  numbers. The first version checked digits only and committed case **CL-03** ("I have
  spent nine years building distributed systems at scale") walked straight through it.
  `test_fabrication_guard` caught that, which is the case set doing its job on a change
  made to please a different measurement.
- **capitalised tokens**, against the vault. F2 stays dead: the vault says Redis, the
  letter may not say Kafka, including at the start of a sentence.
- lowercase prose asserts nothing on its own and is allowed.

**Given up, stated rather than discovered:** class F3 weakens *for letters only* — "led a
cross-functional team" now passes, though the `50+` in it does not — and a lowercase
technology (`pytest`, `npm`) slips. Against that: the résumé keeps the strict rule and is
the document an employer parses, a human reads the letter before M6 sends anything, and §2
reports the letter rather than gating it.

**What was refused, and it is the more useful half.** The last blocker is sentence-initial
capitalised words — `Defining`, `Reliability`, `Interactive`, `Robust`, `Tackling`,
`Scalable`. Capitalisation is the entity proxy and at a sentence start it cannot tell
`Reliability` from `Redis`. **Skipping sentence-initial tokens was refused** — it opens F2
for the price of convenience. **Adding those six words was refused** — it relocates the
failure to the next six, which is this log's own warning. What shipped instead is one
repair attempt judged by the *same* validator: the model is told which word failed and
rewrites. Nothing is relaxed.

```
letters written, live, deepseek-v4-flash
  before any of this          0 / 10
  fact rule, handles buggy    1 / 6     every other failure was a handle
  handles stripped            1 / 6     failures move to sentence openers
  + one repair attempt        3 / 6     = 0.50, the rate gpt-5 reached
```

**Query count: unchanged.** None of this touched the twenty seeded pairs. `cases.json` is
untouched and CL-03 is what caught the number-word omission.

### AMENDED — `words.py` widened by the project owner, and it did not work (2026-08-10)

**No bar moved.** §2 is untouched. What moved is a list the bars measure, and then a
measurement that says the list was not the problem.

**The reversal, stated plainly.** The entry below dated 2026-08-09 refused to widen
`words.py`, on the grounds that it was adding the words that had just failed, immediately
after watching them fail, on the third query of this set. **That refusal is overridden by
the project owner**, and the reason is different rather than louder: run 6 wrote **no
letter at all** for every honest pair, where run 3's rate was half; §2 reports the letter
rate rather than gating it, so widening cannot make the gate lie; and prose quality has
been ruled in scope for the demo, which it explicitly was not on 2026-08-09.

**Applied.** `_CONNECTIVE` 18 words → 78; `ALLOWED` 329 → 389. Grammar only: modals,
discourse adverbs, subordinators, and the verb `apply`. Plus a `LETTER_SYSTEM` rule
forbidding digits anywhere in a letter.

**Still refused, and this is the half that matters.** The nouns that blocked run 3 —
`projects`, `outcomes`, `training`, `background`, `results` — stay out. They are the
OBJECTS of the verbs `words.py` already allows, and that file's own argument for allowing
verbs is *"the object it acts on is still checked"*. An object is a fact. `certified`,
`senior`, `team` and `million` stay out for the reason already written there. The number
rule at `validate.py` is untouched and runs first.

**Then it was measured on nine real pairs, and the letter rate is still 0.00.**

```
round 1, 5 pairs, after the vocabulary widening
  letters written   0/5
  blocked on numbers   3/5   the number 17, 12, 13   <- F4, the rule working
  blocked on nouns     2/5   bring, required, developer, background, centers, experience

round 2, 4 pairs, after the no-digits prompt rule
  letters written   0/4
  blocked on numbers   1/4   the number 13, 14, 17
  blocked on nouns     3/4   backend, full, infrastructure, judged, spans,
                             accountabilities, equally, calls
```

**What this establishes.** The dominant cause of a blocked letter on
`deepseek-v4-flash` was never connective vocabulary. Round 1 says it was the model writing
years-of-experience totals the résumé never states — §4's F4, caught in prose, by a run
nobody seeded for it, exactly as run 4 found. The prompt rule moved that from 3/5 to 1/4
and the failures simply relocated to content nouns.

**And content nouns cannot be added.** `infrastructure`, `backend`, `accountabilities` are
what a paragraph is *about*. Admitting them is admitting the claim. So the honest reading
is structural rather than a tuning problem: a paragraph of connected prose built only from
the words of the claims it cites, plus ~389 grammar words and four proper nouns, is a
sentence the model can rarely write. **Widening the list further is not the fix and this
round is the evidence for that, which is worth more than the words it added.**

**Nothing false shipped in any of the nine.** Every block is the validator doing its job,
0 fabrications, and the résumé still ships without the letter (see the 2026-08-09
amendment below). §2's letter row stays *reported, not gated*, and that remains correct.

**Also observed and NOT a bar question, flagged because somebody will quote it:** three of
those nine résumés were blocked on strip rate (100%, 67%, 100% untraceable), which is 0.33
against §2's ceiling of 0.20. **These are live pairs against real postings, not the gate's
stored ones**, so the number is not comparable to run 6's 0.16 and does not fail anything.
It is a reason to expect the gate re-run to be interesting, not a result.

**Query count: unchanged.** None of this touched the twenty seeded pairs — it is nine live
tailorings against the real pool, which is a different instrument.

### LOGGED — runs 5 and 6: the provider changed, and the set has now been queried six times (2026-08-10)

**The model is no longer the one every number above was measured on.** The OpenAI balance
reached zero, so `TAILOR_MODEL` is `deepseek-v4-flash` and `LLM_BASE_URL` is DeepSeek's
`/beta` host. **Every figure in the run-4 entry below describes `gpt-5` and describes nothing
that runs today.** Read them as history, not as status.

**Run 5 measured nothing and is logged anyway**, because a run that spends money and returns
no number is a fact about the harness. All 40 pairs errored on `HTTPStatusError` — DeepSeek
rejects `response_format: json_schema` — and then a later pair raised `LlmError`, which
`_one()` did not catch, so the fixture died and all seven tests errored. Eleven minutes, no
measurement. Both defects are fixed (`dbb25bb`); neither touched a bar.

**Run 6, the first that measured anything on the new provider:**

```
live seeded cases    18   floor 20        FAIL   inconclusive, NOT a leak
escapes               0   bar 0           held   by class F1:3 F2:4 F3:3 F4:4 F5:4
                                                 0 in 18 bounds the rate at ~17%
pairs attempted      40
errors                3   ceiling 2       FAIL
block rate         0.16   ceiling 0.20    PASS
retention          0.99   spread 0.17     PASS
letters not written 1.00  reported, not gated    — was 0.50 on gpt-5
cost/application $0.0446  ceiling $0.50   PASS   $1.65 over 37, 749s
```

**Two red clauses, one cause.** All three errors are the same defect and every failing
location is an evidence handle — `('bullets', 0, 'evidence_id')`, `('paragraphs', 1,
'evidence_ids', 0)`. `deepseek-v4-flash` returns the handle in a shape pydantic refuses,
twice running. Two of the three were adversarial pairs, so the seeded count fell 20 → 18 and
failed §2's floor as a side effect. **The gate is red on coverage and errors, not on an
escape:** nothing got through, across all five classes.

**Rejected, and named so they are not proposed again as fresh ideas.** `MAX_ERRORS` 2 → 3
would clear one clause. `MIN_LIVE_CASES` 20 → 18 would clear the other. Widening
`TailoredBullet.evidence_id` to accept a non-string would clear both at once and could be
argued as provider tolerance. Each is one line and each is defensible alone — which is what
§8's whole preamble says adaptive analysis feels like from the inside. **This is query six of
the same twenty pairs.** The set cannot absorb a bar edit made to fit a run.

**The change that is legitimate is the model, and it is a correction rather than a tuning.**
CLAUDE.md §7.2 requires a *strong* model for tailoring. `v4-flash` is not one; choosing it was
the deviation, and `deepseek-v4-pro` is on the same key behind the same mechanism. Trying it
changes an input the law already specifies, not a threshold the result is measured against.

**Not blocking the gate, and worse than any number here: no cover letter was written at all.**
1.00, against 0.50 on `gpt-5`. §2 reports this rather than gating it, and that stays correct —
the clause asks whether the system invented anything, and it did not. But a letter stage that
produces nothing on every honest pair is a product failure even with a green fabrication
count, and M6 inherits it.

### LOGGED — the human read of run 4: 0 escapes in 48 documents, `human:MAR` (2026-08-09)

**§1's second non-circular measure, and §2's *escapes found by human read* row, both now have
something behind them.** All 48 documents run 4 wrote — 30 résumés and 18 letters, across
`senior_backend.pdf` and `two_column.pdf` — read against the vaults. **Zero unbacked claims.**

Checked specifically, class by class against §4:

- **F4, metric.** `940ms`, `205ms`, `41`, `2.4 million`, `38%`, `180`, `19%`, `60 million`,
  `4,200` all match their vault entries exactly. **No percentage was computed from absolute
  numbers** (F4-01's shape) and **no number migrated between roles** (F4-02's).
- **F5, skill-role misalignment.** Kubernetes and Terraform never appear in a Tessellate Labs
  bullet; Spark never appears in a Nordlys bullet. The class did not occur.
- **F1, temporal.** No technology postdating a role appeared. The `L-F1` temptations —
  LangChain, LlamaIndex — did not land.
- **Skills.** Every skill on every résumé is a vault claim. Several documents *drop* skills
  (D16 drops Go, Kubernetes, Terraform; D27 drops PyTorch, scikit-learn, MLflow). That is
  selection, which is what the model is for. Nothing was added.

**What this measures, stated with its width.** Zero in 48 read documents puts the 95% upper
bound on the per-document escape rate at roughly **6%** — tighter than the seeded set's ~15%,
and measuring a different thing: the seeded cases prove that fabrications *somebody thought
of* were caught, the read looks for the ones nobody planned. Both are needed and neither is a
claim that the validator does not leak.

**Four quality findings, recorded here and excluded from the fabrication count on the reader's
ruling.** D15 (Bluelight) repeats the idempotency-layer claim in paragraphs 1 and 3; D25
(Cloudflare) repeats the REST-API claim the same way; D19 (Baltic Data Works) repeats the
Spark pipeline claim; D28 (Cloudflare, Desmond) restates E14 and E18 twice in different
phrasings. **Every claim in all four is vault-backed** — they are not fabrications and do not
touch any bar. They are the repetition problem already deferred, now with four named
instances against it, and they say plainly that "no hiring manager would send these as-is".

### LOGGED — run 4: every automated clause passes, and one bar row still has nothing behind it (2026-08-09)

```
live seeded cases  20   escapes 0        bar 0          PASS   bound ~15%, printed
pairs attempted    40   errors  0        ceiling 2      541s at concurrency 4
block rate         0.05                  ceiling 0.20   PASS   résumé
letters not written 0.50                 reported, not gated
retention, live    0.99   spread 0.25    1 bullet stripped in the entire run
cost/application   $0.0573               ceiling $0.50  PASS   $2.29 over 40
```

**Fourth query of the same twenty pairs.** Runs 1–3 are logged below; run 4 follows the
letter/résumé split and no other change. Read every number here knowing that.

**What this run does not establish, and §2 says so in its own row.** *Escapes found by human
read: 0* has **no reading behind it**. §1 pins why that row exists: an automated audit of a
rendered document against the vault uses the same containment rule that produced it and
agrees by construction. The seeded cases are the other non-circular half and they are green,
but "no seeded fabrication in twenty got through" is a narrower claim than "no fabrication
got through". **M5's gate is not green until a human has read the documents**, and this
entry is where that reading gets logged when it happens.

**Half the letters are not being written, and that is not a fabrication result.** Ten of
twenty honest pairs produced no letter. Eight were vocabulary — `includes`, `projects`,
`would`, `outcomes`, `bring`, `Together` — and **two were the rule working exactly as
designed**: `the number 13 is not in the cited evidence` and `the number 17`, on
`senior_backend.pdf`, where the model put a figure into prose that the résumé never states.
That is §4's F4 caught in the letter, by a run nobody seeded for it.

So the letter's vocabulary problem and the letter's fabrication guard are now measurably
different things, which is the argument for not widening `words.py` to make the rate look
better: it would loosen the rule that caught the two real ones.

### AMENDED — the block rate is the résumé's; the letter is reported beside it (2026-08-09)

**Approved by the project owner, on a structural argument rather than on a result.** §2's
ceiling is unchanged at 0.20. What changed is which document it counts.

**Run 3 measured a block rate of 0.45, and 7 of the 9 blocks were a cover-letter paragraph
using a single connective word** — `offer`, `background`, `Together`, `bring`, `training`,
`results`, `addition`. Résumé-only block rate in the same run was **2/20 = 0.10**, and both
of those were legitimate: a 50% strip rate, and two traceable bullets against a floor of
three the vault could have met. Eight of the nine were `two_column.pdf`, whose vault is four
short ML sentences — the narrower the vault, the smaller a letter's legal vocabulary, and
the more the letter rule fires.

The defect this exposed is not the rate. It is that a failing letter **discarded a résumé
that had validated cleanly**, because the stage computed `blocked = résumé or letter`.
Nothing false shipped in any of the seven — the validator did exactly its job — so throwing
away the good document punished the wrong thing.

**Applied:** the two documents are judged separately. The résumé's verdict decides whether
anything ships; a letter that cannot be grounded is not written, the match still moves to
`tailored` with its résumé, and the letter block is recorded on `tailor.generated` with
`letter_bytes: 0`. §2's block rate now counts résumés. The letter's rate is **printed beside
it and not gated**, because §3.7's alert-on-volume applies to something that raises no error.

**Rejected, and it is the more instructive half.** Adding those seven words to `words.py`
would have made run 3 green, and each word individually passes that file's own stated test —
none of them can make a résumé line false. It was refused because it is adding exactly the
words that just failed, immediately after watching them fail, on the third query of this
set. That is the adaptive-analysis shape M4's §8 warns about, and *"each change was
individually defensible"* is what it always looks like from the inside. The letter's
vocabulary problem is now a deferred item with a trigger, owned by M6 where a human reads
the output.

**Query count: three, and this fix follows run 3.** Anyone reading run 4 should know it is
the fourth look at the same twenty pairs.

### LOGGED — the first two live runs, and the defect the block-rate bar caught (2026-08-09)

**No bar moved.** §2 is untouched. What moved was the system §2 measures, which is what a
bar is for — and recording the distinction matters more than the fix, because "the run
failed so we changed a number" and "the run failed so we fixed what it measured" look
identical in a diff.

**Run 1 measured nothing and cost money.** Sequential, 25 minutes, then `httpx.ReadTimeout`
out of the session fixture — all six clauses errored. Two causes: `LLM_TIMEOUT_SECONDS` was
120, written for the cheap extraction model, and a reasoning-class model runs past it; and
the harness had no retry, no concurrency and no per-pair isolation, so one failure took the
whole run. M4 solved the identical shape for 429s and the fix had not been carried over.
Applied: timeout to 300 (a ceiling, not a wait), retry with backoff **in the harness** on
M4's recorded reasoning, concurrency 4, and an explicit error count against a ceiling of 2
so a run can never quietly measure 37 of 40 pairs.

**Run 2 passed five clauses and failed one, correctly.**

```
live seeded cases  20   escapes 0     bar 0        PASS   bound ~15%, printed
pairs attempted    40   errors  0     ceiling 2    486s at concurrency 4
retention, live    0.98            2 bullets stripped in the entire run
cost/application   $0.0577         ceiling $0.50  PASS   $2.31 total
block rate         0.95            ceiling 0.20   FAIL
```

**Every block read `only N traceable bullets, need 6`, with retention at 0.98.** The
validator was barely stripping anything; `TAILOR_MIN_BULLETS = 6` was refusing documents
that were entirely true. `two_column.pdf` holds **four** bullet claims and could never
satisfy a floor of six by any model output; `plain.txt` and `career_changer.docx` hold
three. `senior_backend.pdf` holds exactly six, so the floor demanded the model use every
single one — while the résumé prompt tells it to *prefer fewer, stronger bullets*. The
setting contradicted the prompt and was unsatisfiable for three of four fixtures.

M4 wrote this lesson down one milestone earlier and it was not applied here: **a label the
code cannot reach is not a bar, it is a guaranteed false positive.** Its M5 form: *a floor
the vault cannot reach is not a quality bar, it is a guaranteed block* — and it fails in
the flattering direction, because the printed reason looks like model quality.

**Applied:** the floor is capped at the number of bullet claims the vault holds, and the
default drops from 6 to 3 — the fewest an experience section can carry and still be a
document rather than a stub. Both are changes to the stage, not to §2.

**Rejected:** raising the 0.20 block-rate ceiling, which is the move that would have made
run 2 green without learning anything, and is exactly what §3 step 4 forbids.

**The honest accounting:** this set has now been queried **twice**, and a change was made
between run 2 and run 3 from reading its output. The change was forced by arithmetic — a
floor of six against a vault of four — rather than fitted to a result, which is the only
reason it is not the adaptive analysis M4's §8 warns about. Anyone reading run 3's numbers
should know they follow a fix made after seeing run 2.

### LOGGED — the case set was confirmed, and what the review found (2026-08-09)

**Confirmed by the project owner, `human:MAR`, against `vaults.json` rather than against the
cases' own descriptions.** Thirty-two offline cases and ten live temptations read; all
`expect` values upheld; no case corrected. §2 and §3 are untouched — **no bar moved.**

Three findings recorded at confirmation because they qualify what a green run means:

**Class F3 has one real case, not three.** `F3-02` cites a `skill` claim and `P-01` cites a
handle that does not exist, so rule 1 rejects both before any content check runs; their text
is never examined. §4's taxonomy is therefore covered 2/1/1/3/3 across F1–F5, not 2/2/3/3/3.
**A second genuine F3 case was owed and has been paid**: `F3-03` was drafted, behaviour-checked
against the vault, reviewed and confirmed on its own the same day, and carries its own
`confirmed_by` rather than inheriting the set's — a case nobody read cannot inherit a signature.
It is `two_column.pdf`'s first fabrication case as well, which closes the other half of the
coverage finding below. `F3-02` and `P-01` keep their labels and are worth reading as provenance
cases rather than content ones.

**The retention cases are self-graded, and that is accepted.** `OK-04/05/06` survive on verb
inflections, and `CL-01` on connective words, that the author of `words.py` also chose. The
ruling: which verbs a rewrite may use is a product judgement, not a fabrication question. So
the offline retention number is a **floor on the mechanism** and never a measurement of the
product — the live gate's retention, over a real model's output, is the one that measures
anything, and it is reported rather than gated because there is no prior number to gate against.

**Coverage is lopsided and it was not treated as blocking.** Eleven of fourteen fabrications
are `senior_backend.pdf`. Accepted because the validator is profile-agnostic and the skills
and faithful cases reach the other fixtures; recorded because "it passed" and "it was tried
against every résumé" are different sentences.

**Rejected:** correcting `S-03`, which was authored expecting `PyTorch` to be unfindable and
turned out to be findable. It stands as a happy-path case. The lesson lives in DECISIONS
instead: PROJECT_STATE's note about `two_column.pdf` describes a parse *run*, not the fixture.

*(This file predated every case, every validator and every document. The entry above is the
first thing added to it.)*
