"""§3.5's second rung: vectors for jobs that survived the filters, and only those.

`ensure` takes an explicit list of ids and has **no query that could produce one**. That
is deliberate and it is the structural half of Part 13 rule 6: there is no code path in
this module by which an unfiltered job gets embedded, so "the LLM saw a job a filter
should have dropped" requires someone to pass a different list — a visible diff rather
than a silent regression. The receipt is a database fact the suite asserts on: a
`job_embeddings` row exists only for a job that got past `filters.candidates`.

**The stored model string carries a template version.** `job_embeddings` is keyed
`(job_id, model)`, so that key is what says "this job is done". If the text being
embedded changed — title+company+description today, something else next month — the key
would still say done, and the table would quietly hold two incomparable vector spaces
that one cosine query compares anyway. The version is bumped whenever `text()` changes,
which re-embeds rather than corrupting. Same lesson as M1's `raw_json`: the value the
next run diffs against must mean exactly one thing.
"""

import uuid

from db.models import Job, JobEmbedding
from schemas.job_embedding import EMBEDDING_DIM
from schemas.prefs import Prefs
from schemas.profile import ProfileRead
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from workers import llm
from workers.settings import get_settings

# Bump whenever `text()` changes. See the module docstring.
TEMPLATE_VERSION = "v1"

# Texts per request. The provider takes a list; the ceiling is the request body, not a
# documented limit, and 100 keeps one batch well inside it at 8k-char inputs.
BATCH = 100

# Descriptions run to tens of thousands of characters and the model truncates at its own
# context limit anyway — silently, and from the end, which is where the boilerplate is.
# Cutting here makes the spend predictable instead.
MAX_CHARS = 8_000


def storage_model() -> str:
    """What goes in `job_embeddings.model` — the provider's name plus the template."""
    return f"{get_settings().embed_model}@{TEMPLATE_VERSION}"


def text(job: Job) -> str:
    """What actually gets embedded. Changing this means bumping TEMPLATE_VERSION."""
    return "\n".join(
        [
            job.title,
            job.company,
            ", ".join(job.locations) or (job.location or ""),
            (job.description or "")[:MAX_CHARS],
        ]
    )


def profile_text(profile: ProfileRead, prefs: Prefs, resume: str) -> str:
    """The query side of the comparison.

    The user's target titles are included because a résumé describes what someone *did*
    and prefs describe what they *want next* — a career-changer's résumé embeds to their
    old field, and matching on it alone would show them exactly the jobs they are trying
    to leave.
    """
    return "\n".join([", ".join(prefs.titles), profile.seniority or "", resume[:MAX_CHARS]])


def profile_vector(profile: ProfileRead, prefs: Prefs, resume: str) -> tuple[list[float], int]:
    """Embedded per run and never stored.

    A table or a column would need an invalidation rule keyed on two independently
    mutating inputs — `parsed_json` changes on re-parse, `prefs_json` changes from the
    API — and that staleness check is more code than the call it saves. One call per user
    per run is a few hundred tokens. Trigger to store it: profile embeds exceed ~1% of a
    run's embedding tokens.
    """
    vectors, tokens = llm.embed([profile_text(profile, prefs, resume)])
    return _checked(vectors)[0], tokens


def ensure(session: Session, job_ids: list[uuid.UUID]) -> tuple[int, int]:
    """Embed whichever of these jobs has no vector yet. Returns (written, tokens).

    Commits nothing. The missing-set query runs **before** the provider call, so a second
    run over an unchanged pool spends nothing — which is also what makes the warm and
    cold cost figures the gate reports mean different things.
    """
    if not job_ids:
        return 0, 0

    model = storage_model()
    missing = session.scalars(
        select(Job)
        .where(Job.id.in_(job_ids))
        .where(
            ~select(JobEmbedding.job_id)
            .where(JobEmbedding.job_id == Job.id, JobEmbedding.model == model)
            .exists()
        )
    ).all()
    if not missing:
        return 0, 0

    written = 0
    tokens = 0
    for start in range(0, len(missing), BATCH):
        batch = missing[start : start + BATCH]
        vectors, spent = llm.embed([text(job) for job in batch])
        tokens += spent
        rows = [
            {"job_id": job.id, "model": model, "embedding": vector}
            for job, vector in zip(batch, _checked(vectors), strict=True)
        ]
        # DO NOTHING rather than check-then-act: the composite key is the arbiter, and two
        # workers racing on one job must not raise (Part 13 rule 10).
        written += len(
            session.scalars(
                insert(JobEmbedding)
                .values(rows)
                .on_conflict_do_nothing(index_elements=[JobEmbedding.job_id, JobEmbedding.model])
                .returning(JobEmbedding.job_id)
            ).all()
        )

    session.flush()
    return written, tokens


def _checked(vectors: list[list[float]]) -> list[list[float]]:
    """Refuse a vector of the wrong width rather than letting the column truncate it.

    The `HALFVEC` column rejects a mismatch, but the error surfaces as an opaque insert
    failure halfway through a batch. A model swapped to one with different dimensions is
    a configuration mistake worth naming at the boundary — and it is now a likelier
    mistake than it was, because embeddings and chat can point at different providers.
    """
    for vector in vectors:
        if len(vector) != EMBEDDING_DIM:
            raise llm.LlmError(f"expected {EMBEDDING_DIM}-dimension vectors, got {len(vector)}")
    return vectors
