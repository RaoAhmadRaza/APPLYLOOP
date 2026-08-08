# The M4 bar

**This file is committed before any scored output exists.** That is not ceremony. The
CLAUDE.md §9 gate says the bar must be "set **in advance**", and the only procedure that
makes "in advance" auditable rather than asserted is `git log --diff-filter=A` on this
file predating the first golden run. Everything below is a commitment made while the
numbers were still unknown.

The failure this guards against is specific and it is the easy one to fall into: run the
matcher, look at the score distribution, pick a cut that looks good, declare the bar met.
That procedure passes every time and measures nothing.

---

## 1. What is being measured

**Precision at a threshold**, over hand-labelled (profile, job) pairs:

```
precision = (labelled relevant AND scored >= threshold) / (scored >= threshold)
recall    = (labelled relevant AND scored >= threshold) / (labelled relevant, reachable)
```

"Reachable" excludes pairs whose job a hard filter dropped — those are counted separately
as **filter recall**, because a job dropped before embedding can never be recovered by any
amount of ranking quality, and averaging the two hides the more serious failure.

`borderline` labels are **excluded from precision and recall** and their count is
reported. A rubric that produces many of them is a rubric that needs fixing, not a
matcher that needs tuning.

## 2. The bar

| Quantity | Bar | Why this number |
|---|---|---|
| **precision@threshold** | **≥ 0.80** | `build-sequence-and-phase-architecture.md` §M4 names it: "≥80% of 'Good Fit' are truly relevant". Taken from the doc rather than chosen here, so it cannot be fitted. |
| **recall@threshold** | **≥ 0.50** | Without a recall floor the gate is passable by a matcher that matches nothing: set the threshold to 100, label nothing `good_fit`, and precision is 1.0 by vacuity. This is the single most important line in the file. |
| **filter recall** | **≥ 0.90** | Of pairs labelled relevant, at most one in ten may be killed by a hard filter. Filters are free and irreversible; this is where a silent `WHERE false` shows up. |
| **predicted positives on the reporting split** | **≥ 8** | Below this the run is **inconclusive, not green**. Precision over three items is noise, and a threshold sweep will always find some cut with two-for-two. |
| **candidate pool floor, per profile** | **≥ 200** | The filters must leave a usable pool. Measured before this bar was set: an exact-array location filter left every fixture profile with ≤ 3 candidates out of 1,458, which would have produced excellent precision over nothing. |
| **per-filter cap** | **≤ 0.70 of pool** | No single filter may drop more than 70% of the pool. A filter that drops everything and a filter that drops nothing are the same class of defect and neither raises. |
| **cost per 1,000 jobs scored** | **≤ $2.00** | Denominator pinned in §5. Generous on purpose — this is a ceiling that catches an accident, not a target to optimise toward. |

## 3. How the threshold is chosen

The **procedure** is fixed here; the number is not, and must not be.

1. Score every pair once. Sweep candidate thresholds over the observed scores.
2. On the **tuning split only**, take the *lowest* threshold satisfying both
   `precision ≥ 0.80` and `recall ≥ 0.50`. Lowest, not best — maximising precision on the
   tuning split is how the reporting split gets overfitted.
3. Apply that threshold, unchanged, to the **reporting split**. Those are the numbers the
   gate is judged on.
4. If no threshold satisfies both, **the gate fails**. It is not rescued by lowering the
   bar, and this file is not edited to match the result. It is rescued by fixing the
   matcher, or by recording in `docs/DECISIONS.md` why the bar was wrong with the
   evidence that showed it — which is an amendment, made deliberately, not a side effect.

**Split:** 20 pairs tune, 30 pairs report. Assignment is fixed at file creation and never
re-rolled. Both numbers are reported side by side; a large gap between them *is* the
overfitting signal and is worth more than either number alone.

## 4. Label boundaries

`good_fit` is `score >= threshold`. `reach` is below the threshold on a role one seniority
band above the candidate. `fair` is everything else below. These are fixed here so they
cannot be fitted to the results afterwards, and they are deliberately *not* the labelling
vocabulary — see §6.

## 5. Cost, and its denominator

"Cost per 1,000 jobs scored" is ambiguous by three orders of magnitude, and the
cheapest-sounding reading is the one that means least. Pinned:

- **scored** = pairs that reached the LLM, i.e. `explained`. This is the denominator for
  the headline number.
- Reported alongside, always: **cost per 1,000 jobs in the open pool**, which is what
  actually bills, and the split between **cold** (nothing embedded yet) and **warm**
  (embeddings cached, the steady state). Reporting only the warm number would be a lie by
  omission; reporting only the cold one would be the opposite lie.
- Token counts come from the `match.scored` event. Dollars are computed in the eval from
  the prices below, which sit next to the model name so they cannot go stale unnoticed.

```
embed      $0.02 / 1M tokens     openai/text-embedding-3-small
prompt     $0.05 / 1M tokens     the cheap scoring model (§7.2)
completion $0.40 / 1M tokens
```

## 6. Labelling rules

The labeller sees a profile and a job. They do not see a score, a rank, a cosine, or any
matcher output — the loader asserts the file contains none of those keys.

**Vocabulary is `relevant` / `not_relevant` / `borderline`, never `good_fit`/`fair`/`reach`.**
Deliberately not the product's, so that "is this a job this person should see" stays a
separate question from "where does the score fall". Conflating them makes the score→label
mapping unfalsifiable.

The question to answer is: **would this person be reasonably considered for this role, and
would seeing it be a good use of their attention?** Not "could they do it eventually", not
"is it in the right industry".

Pinned decisions, so that two labellers agree:

1. **Over-qualification is `relevant`** unless the role is more than one band below — a
   senior engineer seeing a mid role is fine, seeing an internship is not.
2. **A remote-friendly role in a city the profile never named is `relevant`**, provided
   the profile does not restrict to on-site.
3. **A profile needing sponsorship + a posting that refuses it is `not_relevant`**,
   regardless of how well the skills fit. This one is not a judgement call. §7.2 calls the
   work-auth filter the most-praised feature in the leading product and its entire value
   is not showing someone jobs they cannot legally take.
4. **A posting written in a language the profile shows no evidence of is `not_relevant`.**
   The pool is roughly a quarter German-language postings; leaving this unstated would let
   them pad the negative class and inflate precision for free.
5. **Title drift** is `relevant` within the same craft (backend → platform → infrastructure
   → SRE), `borderline` across it (backend → data engineering), `not_relevant` outside it
   (backend → sales engineering).
6. **`borderline` is a real answer**, not a cop-out, and is excluded from the metrics.
   Use it when the honest answer is "depends on something the posting does not say".

**Who labels:** a human, recorded per pair as `human:<initials>`. The loader refuses to
count any pair whose labeller is a model. If a model is ever used to pre-sort candidates
for human review, it must be from a different family than `settings.llm_model` and both
are recorded — a set labelled by the thing under test measures self-consistency.

**Agreement:** 15 pairs are double-labelled and raw agreement is reported. Below ~0.8 the
*rubric* is the defect, not the labeller: fix the rules above, re-label, and say so in
`docs/DECISIONS.md`.

## 7. Sampling

Stratified over the **open pool**, drawn independently of the matcher, seeded so the draw
is reproducible and cannot be re-rolled until it looks good. Drawing from the matcher's
own top-N would measure precision over exactly the region the system already believes in.

| Stratum | n per set | Purpose |
|---|---|---|
| `on_topic` | 15 | Passes every hard filter and the title is plausibly on-craft. Where the positives live. |
| `filtered_out` | 15 | Fails **exactly one** hard filter, recorded as `expected_filter`. This is what makes "hard filters demonstrably drop mismatches" assertable per pair rather than in aggregate. |
| `candidate_random` | 10 | Passes every filter, drawn uniformly. Catches retrieval false negatives without needing the pool embedded first. |
| `pool_random` | 10 | Uniform over the whole open pool. The calibration stratum, and the one that screams if a filter has quietly emptied everything. |

**Which filters the set can actually exercise.** Recorded when the first draw was
taken, because it bounds what the filter clause proves. The four fixture profiles state no
preferences, so `remote` and `must_have_keywords` are inactive for them and no pair can
fail on either — those two are covered by the offline polarity suite only. `location`,
`seniority` and `work_auth` all fire and all appear in `filtered_out`. Getting work-auth
in took a fix: a plain shuffle of the single-failure set produces zero of them, because
they are 22–43 rows against fourteen thousand location failures.

**Hard negatives.** At least 60% of the `not_relevant` labels must be marked
`negative_type: hard` — a posting in the same job family as some positive, differing on
exactly one dimension the product claims to handle. A warehouse role against a backend
profile is filler, not a negative: a matcher doing nothing but a keyword grep would score
perfectly against those, and precision over them measures nothing.

**Job payloads are stored in this directory alongside the id.** A pair pinned only by id
rots: `close_missing` or a dedupe promotion removes the job from the pool and precision
changes with no code change. Golden runs seed the stored payloads rather than reading
production.

**Each golden profile gets its own `User`.** `matches` is keyed `(user_id, job_id)` with
no `profile_id`, so without that an eval run writes into the same rows as the real matcher
and the second run reads its own output.

---

## 8. Amendment log

Any change to §2, §3, §4 or §5 after the first scored run must be recorded here with the
date, the evidence that forced it, and a matching entry in `docs/DECISIONS.md`. A silent
edit to this file is the same defect as never having written it.

### OPEN — §7's hard-negative floor contradicts §7's own sampling plan (2026-08-08)

**Not yet amended. A human decides.** Raised before any pair was scored.

§7 requires ≥60% of `not_relevant` labels to be `hard`, and §7 also defines four strata,
**two of which exist to produce structurally easy negatives**: `filtered_out` verifies
that a hard filter dropped something correctly, and `pool_random` is the uniform
calibration draw whose whole job is to scream if a filter has emptied the pool. A
Head of Marketing that failed a seniority filter is an easy negative *by construction*,
and that is the stratum working, not failing.

Measured on the first fully labelled 64-pair set:

```
overall                                    12/32 hard = 38%
on_topic + candidate_random (reached the model)   5/8  hard = 63%
filtered_out                                5/16 hard = 31%
pool_random                                 2/8  hard = 25%
```

The floor exists so that "precision on hard negatives" is a meaningful number, and that
metric is only computed over pairs the matcher actually judged. So the coherent version
is: **scope the ≥60% floor to `on_topic` + `candidate_random`, and add an absolute floor
of ≥10 hard negatives overall** so the set cannot decay toward easy as it grows.

Deliberately left unapplied. Editing a bar to match a result is the exact move §3 forbids,
and the fact that it would be a *correct* edit here is not a reason to make it silently —
it is the reason to write it down and let someone else agree. Until it is applied,
`test_enough_of_the_negatives_are_hard` enforces the original wording on any `confirmed`
set.
