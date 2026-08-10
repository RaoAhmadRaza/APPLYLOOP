"""M4's stage against a real Postgres. **This measures plumbing, not judgement.**

The precision bar lives in `test_matching_live.py` and nowhere else. M3's post-mortem is
that a stubbed model tests the wiring and leaves the thing that matters unmeasured, so
this file deliberately never computes precision — everything here is a property that
holds whatever the model says: what got embedded, what got written, what a second run
does, and what a rescore must refuse to touch.

The model and the embedding endpoint are the only fakes, and only because a test cannot
assert on a live one's output. The session, the constraints, the events and the vector
comparison are all real, per Part 10.
"""

import uuid
from typing import Any

import pytest
from db.models import Event, Job, JobEmbedding, Match, Profile, User
from schemas.enums import MatchStatus, Seniority
from schemas.job_embedding import EMBEDDING_DIM
from schemas.match import MatchFacts
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from workers import llm
from workers.matching import embed, match

THRESHOLD = 60
TOP_N = 10
COMPANY_CAP = 5


@pytest.fixture(autouse=True)
def _model(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    """A deterministic stand-in for both provider calls, and a record of what it saw.

    `seen_jobs` is the assertion surface for Part 13 rule 6: whatever reached the paid
    rung has to be a subset of what the filters allowed.
    """
    state: dict[str, Any] = {
        "seen_jobs": [],
        "embedded": [],
        "facts": MatchFacts(
            met=["Python", "Kubernetes", "PostgreSQL"], missing=["Rust"], summary="Strong overall."
        ),
    }

    def fake_embed(texts: list[str]) -> tuple[list[list[float]], int]:
        state["embedded"].extend(texts)
        # Distinct but deterministic: the first token of the text drives one dimension so
        # the ordering is stable without being uniform.
        # Padded from the constant, not from a literal: the width is a fact the schema
        # owns, and restating it here is how a dimension change becomes a grep.
        pad = [0.0] * (EMBEDDING_DIM - 1)
        return [[float(len(text) % 7 + 1)] + pad for text in texts], 11 * len(texts)

    def fake_complete(schema: Any, *, system: str, user: str, usage: Any = None) -> MatchFacts:
        state["seen_jobs"].append(user)
        if usage is not None:
            usage.append(llm.Usage(prompt_tokens=100, completion_tokens=20))
        result = state["facts"]
        # A list scripts consecutive answers, for the paths that call twice. The last
        # entry repeats, so a test only lists the answers it cares about.
        if isinstance(result, list):
            result = result.pop(0) if len(result) > 1 else result[0]
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(llm, "embed", fake_embed)
    monkeypatch.setattr(llm, "complete_json", fake_complete)
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-a-secret")
    # `Settings` requires both URLs and the stage reads it for the model names. Supplied
    # rather than inherited, because the suite ignores the developer's `.env` — and
    # nothing here connects to Redis, so a container for one string is not worth starting.
    monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
    llm.get_settings.cache_clear()
    return state


def _profile(session: Session, **columns: Any) -> Profile:
    user = User(email=f"{uuid.uuid4()}@example.com", auth_id=str(uuid.uuid4()))
    session.add(user)
    session.flush()
    profile = Profile(
        user_id=user.id,
        master_resume="Senior backend engineer. Python, Kubernetes, PostgreSQL.",
        parsed_json={"basics": {}},
        prefs_json={},
        **columns,
    )
    session.add(profile)
    session.flush()
    return profile


def _job(session: Session, **columns: Any) -> Job:
    defaults: dict[str, Any] = {
        "source": "greenhouse",
        "external_id": f"acme:{uuid.uuid4()}",
        "title": "Backend Engineer",
        "company": "Acme",
        "locations": [],
        "description": "We use Python, Kubernetes and PostgreSQL.",
        "url": "https://boards.greenhouse.io/acme/jobs/1",
        "raw_json": {},
    }
    job = Job(**{**defaults, **columns})
    session.add(job)
    session.flush()
    return job


def _run(session: Session, profile: Profile, **overrides: Any) -> match.MatchResult:
    return match.match_profile(
        session,
        profile,
        **{"threshold": THRESHOLD, "top_n": TOP_N, "company_cap": COMPANY_CAP, **overrides},
    )


def _matches(session: Session, profile: Profile) -> list[Match]:
    return list(
        session.scalars(select(Match).where(Match.user_id == profile.user_id).order_by(Match.id))
    )


def _events(session: Session, event_type: str) -> list[Event]:
    return list(session.scalars(select(Event).where(Event.type == event_type)))


# ---- Part 13 rule 6: nothing paid-for touches a job a filter dropped -------------


def test_a_filtered_job_never_gets_an_embedding(session: Session) -> None:
    """**The structural proof of "hard filters run before embedding".**

    An embedding row is the physical receipt that a job reached a paid stage. Asserting
    its absence needs no call-order spy and survives any refactor that keeps the ladder:
    a job that never had a vector cannot have been ranked, and cannot have been explained.
    """
    profile = _profile(session, locations=["Portland, OR"], seniority=Seniority.SENIOR)
    kept = _job(session, locations=["Portland, OR"])
    wrong_city = _job(session, locations=["Lagos, Nigeria"])
    wrong_band = _job(session, title="Software Engineering Intern", locations=["Portland, OR"])

    _run(session, profile)

    embedded = set(session.scalars(select(JobEmbedding.job_id)).all())
    assert kept.id in embedded
    assert wrong_city.id not in embedded
    assert wrong_band.id not in embedded


def test_the_model_never_sees_a_job_a_filter_dropped(
    session: Session, _model: dict[str, Any]
) -> None:
    """The same rule at the other end of the ladder, asserted on content rather than
    on counts — a count can be right while the wrong rows are in it."""
    profile = _profile(session, locations=["Portland, OR"])
    _job(session, title="Kept Role", locations=["Portland, OR"])
    _job(session, title="Dropped Role", locations=["Lagos, Nigeria"])

    _run(session, profile)

    prompts = "\n".join(_model["seen_jobs"])
    assert "Kept Role" in prompts
    assert "Dropped Role" not in prompts


def test_the_counter_chain_never_increases(session: Session) -> None:
    """§3.5's funnel as an assertion. Every rung must hand the next one no more than it
    received, or the ladder is not a ladder."""
    profile = _profile(session)
    for n in range(5):
        _job(session, external_id=f"acme:{n}", company=f"Company {n}")

    result = _run(session, profile)

    assert result.candidates >= result.shortlisted >= result.explained
    assert result.above_threshold + result.skipped_below + result.failed == result.shortlisted


def test_an_empty_candidate_set_spends_nothing(session: Session, _model: dict[str, Any]) -> None:
    """ "Every job was filtered out" is a normal run, not an error, and must not embed
    the profile or call the model."""
    profile = _profile(session, locations=["Portland, OR"])
    _job(session, locations=["Lagos, Nigeria"])

    result = _run(session, profile)

    assert result.candidates == 0
    assert _model["embedded"] == []
    assert _model["seen_jobs"] == []
    assert result.embed_tokens == 0
    assert result.prompt_tokens == 0


# ---- idempotency -----------------------------------------------------------------


def test_a_second_run_writes_no_second_match(session: Session) -> None:
    """Keyed `(user_id, job_id)`; the unique constraint is the arbiter (Part 13 rule 10)."""
    profile = _profile(session)
    _job(session)

    _run(session, profile)
    _run(session, profile)

    assert session.scalar(select(func.count()).select_from(Match)) == 1


def test_a_second_run_embeds_nothing_and_costs_nothing(
    session: Session, _model: dict[str, Any]
) -> None:
    """`job_embeddings`' composite key is the idempotency key, and the missing-set query
    runs before the provider call — which is what makes the warm and cold cost figures
    the gate reports mean two different things."""
    profile = _profile(session)
    _job(session)
    _run(session, profile)
    _model["embedded"].clear()

    second = _run(session, profile)

    assert second.embedded_new == 0
    # The profile is still embedded each run — one call, deliberately not stored.
    assert len(_model["embedded"]) <= 1


def test_a_job_already_scored_is_not_paid_for_twice(
    session: Session, _model: dict[str, Any]
) -> None:
    profile = _profile(session)
    _job(session)
    _run(session, profile)
    _model["seen_jobs"].clear()

    second = _run(session, profile)

    assert second.candidates == 0
    assert _model["seen_jobs"] == []


# ---- the rescore must not undo a human decision ----------------------------------


@pytest.mark.parametrize(
    "status", [MatchStatus.TAILORED, MatchStatus.QUEUED, MatchStatus.APPROVED, MatchStatus.APPLIED]
)
def test_a_rescore_never_drags_a_decided_match_backwards(
    session: Session, status: MatchStatus
) -> None:
    """M6 has messaged a human on the strength of this row and they may have acted on it.

    Without the `where` on the conflict arm, a routine rescore silently undoes that.
    """
    profile = _profile(session)
    job = _job(session)
    session.add(
        Match(user_id=profile.user_id, job_id=job.id, score=91, status=status, reasons_json={})
    )
    session.flush()

    _run(session, profile)

    row = _matches(session, profile)[0]
    assert row.status == status
    assert row.score == 91


def test_a_discovered_match_is_rescored_in_place(session: Session) -> None:
    """The other half: a match nobody has acted on must pick up a new score."""
    profile = _profile(session)
    job = _job(session)
    session.add(
        Match(
            user_id=profile.user_id,
            job_id=job.id,
            score=None,
            status=MatchStatus.DISCOVERED,
            reasons_json={},
        )
    )
    session.flush()

    _run(session, profile)

    assert _matches(session, profile)[0].score == 75


# ---- what a written match carries ------------------------------------------------


def test_every_written_match_carries_a_reason(session: Session) -> None:
    """The gate clause, as a property that holds regardless of what the model said."""
    profile = _profile(session)
    _job(session)

    _run(session, profile)

    reasons = _matches(session, profile)[0].reasons_json
    assert reasons["summary"]
    assert reasons["met"] or reasons["missing"]
    assert reasons["threshold"] == THRESHOLD
    assert reasons["embed_model"].endswith(f"@{embed.TEMPLATE_VERSION}")


def test_below_the_threshold_is_written_as_skipped(
    session: Session, _model: dict[str, Any]
) -> None:
    """ "Only above-threshold matches proceed" as a database fact rather than a
    convention: M5 selects WHERE status = 'discovered' and cannot see these."""
    _model["facts"] = MatchFacts(met=["Python"], missing=["Rust", "Go", "Elixir"], summary="Thin.")
    profile = _profile(session)
    _job(session)

    result = _run(session, profile)

    row = _matches(session, profile)[0]
    assert row.score == 25
    assert row.status == MatchStatus.SKIPPED
    assert result.above_threshold == 0
    assert result.skipped_below == 1


def test_a_posting_stating_no_requirements_scores_null_and_is_skipped(
    session: Session, _model: dict[str, Any]
) -> None:
    """Unknown, not zero — a posting that told us nothing must not outrank a bad fit."""
    _model["facts"] = MatchFacts(met=[], missing=[], summary="The posting lists no requirements.")
    profile = _profile(session)
    _job(session)

    _run(session, profile)

    row = _matches(session, profile)[0]
    assert row.score is None
    assert row.label is None
    assert row.status == MatchStatus.SKIPPED


def test_an_empty_met_partition_is_re_asked_once(
    session: Session, _model: dict[str, Any]
) -> None:
    """`met=[]` beside a non-empty `missing` scores 0 by arithmetic and reads as a
    rejection nobody can check. The live gate saw it on a different relevant pair each
    run, so it is the model sampling badly rather than the posting being bad."""
    _model["facts"] = [
        MatchFacts(met=[], missing=["Rust"], summary="Nothing matched."),
        MatchFacts(met=["Python", "Go", "SQL"], missing=["Rust"], summary="Strong overall."),
    ]
    profile = _profile(session)
    _job(session)

    _run(session, profile)

    row = _matches(session, profile)[0]
    assert row.score == 75
    assert row.status == MatchStatus.DISCOVERED
    # Exactly two calls for the one job: the first answer and its retry.
    assert len(_model["seen_jobs"]) == 2


def test_a_second_empty_met_partition_is_believed(
    session: Session, _model: dict[str, Any]
) -> None:
    """The retry fixes a sampling artefact, not the score. A profile that genuinely
    evidences none of a posting's requirements still scores 0 and is still skipped —
    otherwise this would be a way of deleting every honest rejection."""
    _model["facts"] = MatchFacts(met=[], missing=["Rust", "Elixir"], summary="No overlap.")
    profile = _profile(session)
    _job(session)

    _run(session, profile)

    row = _matches(session, profile)[0]
    assert row.score == 0
    assert row.status == MatchStatus.SKIPPED
    assert len(_model["seen_jobs"]) == 2


# ---- one bad posting must not kill a run -----------------------------------------


def test_a_failed_explain_is_recorded_and_the_run_continues(
    session: Session, _model: dict[str, Any]
) -> None:
    """The other thirty-nine jobs in a shortlist have already been paid for."""
    _model["facts"] = llm.LlmError("response failed validation at [('met',)]")
    profile = _profile(session)
    _job(session)

    result = _run(session, profile)

    assert result.failed == 1
    assert result.explained == 0
    assert _matches(session, profile)[0].score is None
    assert len(_events(session, "match.explain_failed")) == 1


# ---- the shortlist -----------------------------------------------------------------


def test_no_single_employer_can_fill_the_shortlist(session: Session) -> None:
    """One company was 55% of the open pool. Uncapped, top-N is a sampler for whoever
    posts the most, and every match the user sees comes from one board."""
    profile = _profile(session)
    for n in range(8):
        _job(session, external_id=f"big:{n}", company="BigCo")
    for n in range(3):
        _job(session, external_id=f"small:{n}", company=f"SmallCo {n}")

    _run(session, profile, company_cap=2)

    companies = [
        session.get(Job, row.job_id).company  # type: ignore[union-attr]
        for row in _matches(session, profile)
    ]
    assert companies.count("BigCo") == 2
    assert len(companies) == 5


def test_the_shortlist_is_capped_at_top_n(session: Session) -> None:
    profile = _profile(session)
    for n in range(12):
        _job(session, external_id=f"acme:{n}", company=f"Company {n}")

    result = _run(session, profile, top_n=4)

    assert result.shortlisted == 4
    assert len(_matches(session, profile)) == 4


# ---- the events ------------------------------------------------------------------


def test_the_filter_funnel_is_recorded_on_every_run(session: Session) -> None:
    """§3.7: a stage that quietly drops everything raises no exception. The funnel is
    the only thing that would show it, so it is written on every run, not just in tests."""
    profile = _profile(session, locations=["Portland, OR"])
    _job(session, locations=["Portland, OR"])
    _job(session, locations=["Lagos, Nigeria"])

    _run(session, profile)

    payload = _events(session, "match.filtered")[0].payload_json
    assert payload["pool"] == 2
    assert payload["dropped_location"] == 1
    assert payload["candidates"] == 1
    assert "dropped_salary" not in payload


def test_the_scored_event_carries_the_tokens_the_run_spent(session: Session) -> None:
    """Doing double duty: the cost measurement, and the live gate's anti-stub assertion.

    A stubbed model reports zero tokens, so `prompt_tokens > 0` in the live suite cannot
    pass without a real call.
    """
    profile = _profile(session)
    _job(session)

    _run(session, profile)

    payload = _events(session, "match.scored")[0].payload_json
    assert payload["prompt_tokens"] == 100
    assert payload["completion_tokens"] == 20
    assert payload["embed_tokens"] > 0
    assert payload["threshold"] == THRESHOLD
    assert payload["elapsed_ms"] >= 0


def test_the_events_belong_to_the_user_they_are_about(session: Session) -> None:
    """Unlike ingestion, these are per-user facts: one pool serves everyone, but a score
    is about one person."""
    profile = _profile(session)
    _job(session)

    _run(session, profile)

    assert _events(session, "match.scored")[0].user_id == profile.user_id
    assert _events(session, "match.filtered")[0].user_id == profile.user_id
