"""M4: the deduped job pool and one profile in, scored `matches` rows out.

The stage reads `jobs`, `profiles` and `matches`, and writes `matches` and
`job_embeddings`. It imports `db`, `schemas`, `workers.llm`, `workers.seniority` and
`workers.settings` — **and nothing from `workers/scraping/` or `workers/profiles/`**,
which is §3.1. Where a helper is genuinely shared it moved up rather than being copied:
`seniority.band` came out of `profiles/derive.py` the day this package needed it.

§3.5's ladder is the module split, and the order is the cost architecture:

    filters.py   SQL only. Free. Runs first and hands on a list of ids.
    embed.py     one provider call per batch. Cents. Takes an explicit id list.
    prompt.py    the gap-analysis instructions. Prose, versioned like code.
    score.py     pure functions from the model's facts to a score and a label.
    match.py     sequences the five and writes the rows.

**The wrong order is unrepresentable, not merely discouraged.** Every step after
`filters` takes ids or rows as an argument and never a `Session` it could re-query the
pool from, so "the LLM saw a job a filter should have dropped" requires passing a
different list — a visible diff rather than a silent regression. The receipt is a
database fact: a `job_embeddings` row exists only for a job that survived the filters,
which is what the suite asserts instead of spying on call order.

**The LLM extracts facts; Python computes the score.** Same split as M3, for a sharper
reason here: Part 14 says the threshold must be set empirically by the golden set, and a
threshold is only meaningful against a stable score distribution. The model partitions
the posting's stated requirements into met and missing; `score.py` does the arithmetic.

**Unknown never drops.** Every filter in `filters.py` passes when either side is silent —
`jobs.remote_mode IS NULL` on 87% of the real pool, and most postings state no seniority
and no work-auth requirement at all. The polarity is DECISIONS.md's `is_remote=False`
entry applied five more times: a filter that reads silence as a failure is how a stage
quietly returns nothing while every test stays green.
"""
