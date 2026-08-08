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

*(No entries yet. This file predates every case, every validator and every document.)*
