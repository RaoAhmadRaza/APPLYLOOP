"""The stage: one profile against the deduped pool, `matches` rows out.

A plain function over `(session, profile)` for the reason `ingest_company` and
`parse_profile` are: Part 10's rule is seed, run, assert the delta, and a task would put
a broker between the test and the assertion. Commits nothing.

§3.5's ladder, in order, each rung handing the next a list it cannot widen:

    filters.candidates   free      the whole pool -> what is worth paying for
    embed.ensure         cents     vectors, only for jobs that survived
    _shortlist           free      cosine, top-N, capped per company
    llm.complete_json    money     one call per shortlisted job

The per-company cap is not a nicety. One employer was 55% of the open pool the day this
was written, and an uncapped top-N is a sampler for whoever posts the most — the user
gets forty matches from one company and no way to tell that is what happened.
"""

import time
import uuid
from dataclasses import dataclass

from db.events import record
from db.models import Job, JobEmbedding, Match, Profile
from schemas.enums import MatchStatus
from schemas.match import MatchFacts
from schemas.prefs import Prefs
from schemas.profile import ProfileRead
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from workers import llm
from workers import seniority as bands
from workers.matching import embed, filters, prompt, score

# Statuses a rescore may overwrite. **Not optional.** Without it a re-run drags an
# `approved` or `applied` match back to `discovered`, and M6's human approval — already
# messaged, possibly already acted on — is silently undone. This is the conditional
# UPDATE the `Match` model's own docstring prescribes, expressed as an ON CONFLICT.
_OVERWRITABLE = (MatchStatus.DISCOVERED.value, MatchStatus.SKIPPED.value)


@dataclass(frozen=True)
class MatchResult:
    candidates: int
    embedded_new: int
    shortlisted: int
    explained: int
    above_threshold: int
    skipped_below: int
    failed: int
    embed_tokens: int
    prompt_tokens: int
    completion_tokens: int

    @property
    def changed(self) -> int:
        return self.above_threshold + self.skipped_below


def match_profile(
    session: Session, profile: Profile, *, threshold: int, top_n: int, company_cap: int
) -> MatchResult:
    """Score one profile against the pool. Commits nothing.

    `threshold` is a required argument rather than a setting read here, so that the
    Part 14 interlock lives in exactly one place — the task — and this function cannot
    be called with an invented default.
    """
    started = time.monotonic()
    view = ProfileRead.model_validate(profile)
    prefs = Prefs.model_validate(profile.prefs_json or {})
    resume = profile.master_resume or ""

    candidate_ids, funnel = filters.candidates(session, view, prefs)
    record(
        session,
        "match.filtered",
        {"profile_id": str(profile.id), **funnel.as_payload()},
        user_id=profile.user_id,
    )

    written, embed_tokens = embed.ensure(session, candidate_ids)
    if candidate_ids:
        query, profile_tokens = embed.profile_vector(view, prefs, resume)
        embed_tokens += profile_tokens
        shortlist = _shortlist(session, candidate_ids, query, top_n=top_n, company_cap=company_cap)
    else:
        shortlist = []

    usage: list[llm.Usage] = []
    counts = dict.fromkeys(("above", "below", "failed"), 0)
    for job, similarity in shortlist:
        facts = _explain(session, profile, job, view, prefs, resume, usage)
        if facts is None:
            counts["failed"] += 1
            _upsert(session, profile, job, None, None, {}, MatchStatus.SKIPPED)
            continue

        value = score.score(facts)
        delta = bands.distance(view.seniority, bands.band(job.title))
        payload = score.reasons(
            facts,
            similarity=similarity,
            seniority_delta=delta,
            filters_passed=filters.active(view, prefs),
            threshold=threshold,
            model=llm.get_settings().llm_model,
            embed_model=embed.storage_model(),
        )
        label = score.label(value, threshold=threshold, seniority_delta=delta)
        # Below the bar is written as `skipped`, so "only above-threshold matches
        # proceed" is a database fact: M5 selects WHERE status = 'discovered' and cannot
        # see these, using the index that already exists.
        above = value is not None and value >= threshold
        counts["above" if above else "below"] += 1
        _upsert(
            session,
            profile,
            job,
            value,
            label,
            payload,
            MatchStatus.DISCOVERED if above else MatchStatus.SKIPPED,
        )

    result = MatchResult(
        candidates=funnel.candidates,
        embedded_new=written,
        shortlisted=len(shortlist),
        explained=len(shortlist) - counts["failed"],
        above_threshold=counts["above"],
        skipped_below=counts["below"],
        failed=counts["failed"],
        embed_tokens=embed_tokens,
        prompt_tokens=sum(entry.prompt_tokens for entry in usage),
        completion_tokens=sum(entry.completion_tokens for entry in usage),
    )
    record(
        session,
        "match.scored",
        {
            "profile_id": str(profile.id),
            **{key: getattr(result, key) for key in result.__dataclass_fields__},
            "threshold": threshold,
            "elapsed_ms": round((time.monotonic() - started) * 1000),
            "embed_model": embed.storage_model(),
            "llm_model": llm.get_settings().llm_model,
        },
        user_id=profile.user_id,
    )
    session.flush()
    return result


def _shortlist(
    session: Session,
    job_ids: list[uuid.UUID],
    query: list[float],
    *,
    top_n: int,
    company_cap: int,
) -> list[tuple[Job, float]]:
    """The N nearest candidates, no more than `company_cap` from any one employer.

    `ORDER BY ... , jobs.id` because an unbroken tie reorders between runs, which would
    send a different set to the paid rung each time and make the golden set measure the
    ordering rather than the matcher.
    """
    distance = JobEmbedding.embedding.cosine_distance(query)
    ranked = (
        select(
            Job,
            distance.label("distance"),
            func.row_number()
            .over(partition_by=Job.company, order_by=(distance, Job.id))
            .label("rank"),
        )
        .join(JobEmbedding, JobEmbedding.job_id == Job.id)
        .where(Job.id.in_(job_ids), JobEmbedding.model == embed.storage_model())
        .subquery()
    )
    rows = session.execute(
        select(ranked)
        .where(ranked.c.rank <= company_cap)
        .order_by(ranked.c.distance, ranked.c.id)
        .limit(top_n)
    ).all()
    ids = [row.id for row in rows]
    jobs = {found.id: found for found in session.scalars(select(Job).where(Job.id.in_(ids))).all()}
    # Cosine *distance* is 1 - similarity; the reason payload records similarity because
    # that is the direction a human reads.
    return [(jobs[row.id], round(1.0 - float(row.distance), 4)) for row in rows]


def _explain(
    session: Session,
    profile: Profile,
    job: Job,
    view: ProfileRead,
    prefs: Prefs,
    resume: str,
    usage: list[llm.Usage],
) -> MatchFacts | None:
    """One paid call. A failure is recorded and skipped, never fatal.

    One malformed posting must not kill a forty-job run: the other thirty-nine have
    already been paid for by the time it fails.
    """
    try:
        return llm.complete_json(
            MatchFacts,
            system=prompt.SYSTEM,
            user=prompt.build(job, view, prefs, resume),
            usage=usage,
        )
    except llm.LlmError as error:
        record(
            session,
            "match.explain_failed",
            {"profile_id": str(profile.id), "job_id": str(job.id), "error": str(error)[:500]},
            user_id=profile.user_id,
        )
        return None


def _upsert(
    session: Session,
    profile: Profile,
    job: Job,
    value: int | None,
    label: object,
    reasons: dict[str, object],
    status: MatchStatus,
) -> None:
    """Idempotent on `(user_id, job_id)`, and refuses to undo a decision.

    The unique constraint is the arbiter, never a prior SELECT (Part 13 rule 10). The
    `where` on the conflict arm is what stops a rescore from dragging an approved match
    back to `discovered`.
    """
    statement = insert(Match).values(
        user_id=profile.user_id,
        job_id=job.id,
        score=value,
        label=label,
        reasons_json=reasons,
        status=status.value,
    )
    session.execute(
        statement.on_conflict_do_update(
            index_elements=[Match.user_id, Match.job_id],
            set_={
                "score": statement.excluded.score,
                "label": statement.excluded.label,
                "reasons_json": statement.excluded.reasons_json,
                "status": statement.excluded.status,
                "updated_at": func.now(),
            },
            where=Match.status.in_(_OVERWRITABLE),
        )
    )
