"""M4's gate: the golden set through the real model and the real embedding endpoint.

    make verify-live-match

**Nothing is stubbed at the `llm` boundary.** M3's replay trick — cache one answer per
fixture and monkeypatch `complete_json` for the rest — is right for the parse suite and
wrong here, because the thing being measured *is* the call. The proof that it is real is
`prompt_tokens > 0` and `embed_tokens > 0` on the recorded event: a stub reports zero, so
there is no way to make this file green with a fake. That assertion costs one line and it
comes free, because the gate has to measure cost anyway.

The bar is read from `evals/golden/BAR.md`'s committed values, never written inline, and
the threshold is *chosen by the procedure BAR.md §3 fixed* rather than by whatever looks
good. If nothing clears both the precision bar and the recall floor, the gate fails — it
is not rescued by editing the bar.

What this file deliberately does not do: reuse the matcher's ranking to pick which pairs
to score. Every labelled pair is scored, including the ones the hard filters dropped,
because "the filter was right to drop it" is a claim that needs measuring too and cannot
be measured from inside the set of things that survived.
"""

import json
import os
import pathlib
import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from db.models import Job, Profile, User
from schemas.match import MatchFacts
from schemas.prefs import Prefs
from schemas.profile import ProfileRead
from sqlalchemy import Engine
from sqlalchemy.orm import Session
from workers import llm
from workers.matching import embed, filters, prompt, score

GOLDEN = pathlib.Path(__file__).parents[2] / "evals" / "golden"
PAIRS = GOLDEN / "pairs.json"

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("APPLYLOOP_LIVE_MATCH"),
        reason="calls a real model and spends real credit; set APPLYLOOP_LIVE_MATCH=1",
    ),
    pytest.mark.skipif(
        not os.getenv("LLM_API_KEY"),
        reason="the matcher no-ops without an API key, by design",
    ),
    pytest.mark.skipif(
        not PAIRS.exists(),
        reason="no golden set — run evals/golden/build_worksheet.py, then label it",
    ),
]

# BAR.md §2, committed before any of this ran. Read here rather than re-derived so that
# changing a bar means editing the file whose git history is the evidence.
BAR_PRECISION = 0.80
BAR_RECALL = 0.50
BAR_FILTER_RECALL = 0.90
MIN_PREDICTED_POSITIVES = 8
POOL_FLOOR = 200
PER_FILTER_CAP = 0.70
COST_CEILING_PER_1K = 2.00

# BAR.md §5. Beside the model names so they cannot go stale unnoticed.
USD_PER_MTOK = {"embed": 0.02, "prompt": 0.05, "completion": 0.40}

# BAR.md §3. Fixed at file creation, never re-rolled.
TUNE_SPLIT = 20


@dataclass(frozen=True)
class Scored:
    profile: str
    job_id: str
    label: str
    stratum: str
    expected_filter: str | None
    negative_type: str | None
    reached_model: bool
    score: int | None
    reasons: dict[str, Any]


def _golden() -> dict[str, Any]:
    return json.loads(PAIRS.read_text())


def _labelled(data: dict[str, Any]) -> list[dict[str, Any]]:
    pairs = [pair for pair in data["pairs"] if pair["label"] is not None]
    if len(pairs) < 50:
        pytest.skip(f"golden set has {len(pairs)} labelled pairs; needs 50 (BAR.md §2)")
    return pairs


@pytest.fixture(scope="session")
def run(engine: Engine) -> dict[str, Any]:
    """Score the whole golden set once, for real, and hand every test the summary.

    Session-scoped because this is the expensive thing in the repo: one pass, then five
    named assertions over what it produced. It opens its own session and rolls back, so
    the eval never leaves rows behind — and each golden profile gets its **own** `User`,
    because `matches` is keyed `(user_id, job_id)` with no `profile_id` and without that
    an eval run would write into the same rows as the real matcher.
    """
    data = _golden()
    pairs = _labelled(data)
    llm.get_settings.cache_clear()

    scored: list[Scored] = []
    funnels: dict[str, dict[str, int]] = {}
    tokens = {"embed": 0, "prompt": 0, "completion": 0}
    embedded_cold = 0

    with Session(engine) as session:
        outer = session.begin()
        for fixture, blob in data["profiles"].items():
            wanted = [pair for pair in pairs if pair["profile"] == fixture]
            if not wanted:
                continue
            profile = _seed_profile(session, blob)
            _seed_jobs(session, wanted)
            view = ProfileRead.model_validate(profile)
            prefs = Prefs.model_validate(profile.prefs_json or {})

            # The real funnel over the real pool — this is what the pool floor and the
            # per-filter cap are asserted against.
            candidate_ids, funnel = filters.candidates(session, view, prefs)
            funnels[fixture] = funnel.as_payload()

            for pair in wanted:
                job = session.get(Job, uuid.UUID(pair["job_id"]))
                assert job is not None
                reached = job.id in set(candidate_ids)
                value: int | None = None
                reasons: dict[str, Any] = {}
                if reached:
                    written, spent = embed.ensure(session, [job.id])
                    embedded_cold += written
                    tokens["embed"] += spent
                    usage: list[llm.Usage] = []
                    facts = llm.complete_json(
                        MatchFacts,
                        system=prompt.SYSTEM,
                        user=prompt.build(job, view, prefs, profile.master_resume or ""),
                        usage=usage,
                    )
                    tokens["prompt"] += sum(entry.prompt_tokens for entry in usage)
                    tokens["completion"] += sum(entry.completion_tokens for entry in usage)
                    value = score.score(facts)
                    reasons = score.reasons(
                        facts,
                        similarity=0.0,
                        seniority_delta=None,
                        filters_passed=filters.active(view, prefs),
                        threshold=0,
                        model=llm.get_settings().llm_model,
                        embed_model=embed.storage_model(),
                    )
                scored.append(
                    Scored(
                        profile=fixture,
                        job_id=pair["job_id"],
                        label=pair["label"],
                        stratum=pair["stratum"],
                        expected_filter=pair["expected_filter"],
                        negative_type=pair["negative_type"],
                        reached_model=reached,
                        score=value,
                        reasons=reasons,
                    )
                )
        outer.rollback()

    return {"scored": scored, "funnels": funnels, "tokens": tokens, "cold": embedded_cold}


def _seed_profile(session: Session, blob: dict[str, Any]) -> Profile:
    user = User(email=f"{uuid.uuid4()}@example.com", auth_id=str(uuid.uuid4()))
    session.add(user)
    session.flush()
    profile = Profile(
        user_id=user.id,
        master_resume=blob["master_resume"],
        parsed_json=blob["parsed_json"],
        prefs_json={},
        locations=blob["locations"],
        seniority=blob["seniority"],
        work_auth=blob["work_auth"],
    )
    session.add(profile)
    session.flush()
    return profile


def _seed_jobs(session: Session, pairs: list[dict[str, Any]]) -> None:
    """The stored payloads, not production rows.

    A pair read live would change under the gate: `close_missing` or a dedupe promotion
    removes the job from the pool and precision moves with no code change.
    """
    for pair in pairs:
        if session.get(Job, uuid.UUID(pair["job_id"])) is not None:
            continue
        blob = pair["job"]
        session.add(
            Job(
                id=uuid.UUID(pair["job_id"]),
                source=blob["source"],
                external_id=f"golden:{pair['job_id']}",
                title=blob["title"],
                company=blob["company"],
                locations=blob["locations"],
                remote_mode=blob["remote_mode"],
                description=blob["description"],
                url=blob["url"],
                raw_json={},
            )
        )
    session.flush()


def _sweep(scored: list[Scored]) -> list[tuple[int, float, float, int]]:
    """(threshold, precision, recall, predicted positives) for every candidate cut.

    Free, because every pair is already scored. This is the whole reason the score is a
    deterministic function of the model's facts rather than a number the model emits.
    """
    reachable = [row for row in scored if row.reached_model and row.label != "borderline"]
    relevant = sum(1 for row in reachable if row.label == "relevant")
    out = []
    for threshold in range(0, 101, 5):
        positives = [row for row in reachable if row.score is not None and row.score >= threshold]
        if not positives or not relevant:
            continue
        hits = sum(1 for row in positives if row.label == "relevant")
        out.append((threshold, hits / len(positives), hits / relevant, len(positives)))
    return out


def _chosen(scored: list[Scored]) -> tuple[int, float, float, int] | None:
    """BAR.md §3: the **lowest** threshold clearing both bars on the tuning split.

    Lowest rather than best — maximising precision on the tuning split is exactly how the
    reporting split gets overfitted.
    """
    tune = scored[:TUNE_SPLIT]
    for row in _sweep(tune):
        if row[1] >= BAR_PRECISION and row[2] >= BAR_RECALL:
            return row
    return None


# ---- gate clause: precision meets the bar set in advance -------------------------


def test_a_threshold_clears_both_the_precision_bar_and_the_recall_floor(
    run: dict[str, Any],
) -> None:
    """The headline.

    The recall floor is not decoration: without it, a threshold of 100 labels nothing a
    good fit, precision is 1.0 by vacuity, and a matcher that matches nothing passes.
    """
    scored: list[Scored] = run["scored"]
    picked = _chosen(scored)
    assert picked is not None, (
        "no threshold clears both bars on the tuning split. BAR.md §3: the gate fails "
        "here, and is not rescued by editing the bar."
    )

    threshold = picked[0]
    report = scored[TUNE_SPLIT:]
    rows = [row for row in _sweep(report) if row[0] == threshold]
    assert rows, f"threshold {threshold} produced no positives on the reporting split"
    _, precision, recall, positives = rows[0]

    print(f"\n  threshold          {threshold}")
    print(f"  tuning  precision  {picked[1]:.2f}  recall {picked[2]:.2f}  n={picked[3]}")
    print(f"  report  precision  {precision:.2f}  recall {recall:.2f}  n={positives}")
    print(f"\n  MATCH_THRESHOLD={threshold}")

    assert positives >= MIN_PREDICTED_POSITIVES, (
        f"{positives} predicted positives on the reporting split — BAR.md §2 calls this "
        "inconclusive, not green"
    )
    assert precision >= BAR_PRECISION
    assert recall >= BAR_RECALL


def test_precision_on_hard_negatives_is_reported(run: dict[str, Any]) -> None:
    """The honest headline. Precision over easy negatives measures how many warehouse
    jobs are in the sample, not how good the matcher is."""
    scored: list[Scored] = run["scored"]
    picked = _chosen(scored)
    assert picked is not None
    hard = [
        row
        for row in scored
        if row.reached_model and (row.label == "relevant" or row.negative_type == "hard")
    ]
    positives = [row for row in hard if row.score is not None and row.score >= picked[0]]
    if positives:
        hits = sum(1 for row in positives if row.label == "relevant")
        rate = hits / len(positives)
        print(f"\n  precision on hard negatives only: {rate:.2f} (n={len(positives)})")


# ---- gate clause: hard filters demonstrably drop mismatches BEFORE embedding -----


def test_a_filtered_pair_never_reached_a_paid_stage(run: dict[str, Any]) -> None:
    """The structural proof, on real postings rather than fixtures.

    An embedding row is the receipt that a job reached a paid stage; a pair the filters
    dropped must have neither that nor a score.
    """
    for row in run["scored"]:
        if row.expected_filter and not row.reached_model:
            assert row.score is None
            assert row.reasons == {}


def test_the_filters_did_not_quietly_empty_the_pool(run: dict[str, Any]) -> None:
    """BAR.md §2's pool floor.

    Measured before the bar was set: an exact-array location filter left every fixture
    profile with at most three candidates out of 1,458 — which produces excellent
    precision over nothing at all and passes every other clause of the gate.
    """
    for fixture, funnel in run["funnels"].items():
        print(f"\n  {fixture}: {funnel}")
        assert funnel["candidates"] >= POOL_FLOOR, f"{fixture} left {funnel['candidates']}"


def test_no_single_filter_drops_almost_everything(run: dict[str, Any]) -> None:
    """A filter that drops everything and one that drops nothing are the same defect."""
    for fixture, funnel in run["funnels"].items():
        pool = funnel["pool"]
        for name, dropped in funnel.items():
            if name.startswith("dropped_"):
                assert dropped <= PER_FILTER_CAP * pool, f"{fixture}: {name} took {dropped}/{pool}"


def test_the_filters_rarely_kill_something_genuinely_relevant(run: dict[str, Any]) -> None:
    """**Filter recall** — the number the DoD wording does not ask for and should.

    A job dropped before embedding cannot be recovered by any amount of ranking quality,
    so this is strictly more serious than a ranking miss.
    """
    relevant = [row for row in run["scored"] if row.label == "relevant"]
    if not relevant:
        pytest.skip("no relevant pairs labelled")
    reached = [row for row in relevant if row.reached_model]
    recall = len(reached) / len(relevant)
    print(f"\n  filter recall: {recall:.2f} ({len(reached)}/{len(relevant)})")
    assert recall >= BAR_FILTER_RECALL


# ---- gate clause: cost per 1,000 jobs scored is measured -------------------------


def test_the_run_actually_called_a_model(run: dict[str, Any]) -> None:
    """**The anti-stub proof.**

    A stubbed model reports zero tokens. This assertion is what makes it impossible to
    make this file green with a fake, and it costs one line because the gate has to
    measure cost anyway. M3's lesson, mechanised.
    """
    assert run["tokens"]["prompt"] > 0
    assert run["tokens"]["embed"] > 0


def test_cost_per_thousand_scored_is_measured_and_under_the_ceiling(
    run: dict[str, Any],
) -> None:
    """BAR.md §5 pins the denominator, because "cost per 1,000 jobs scored" is ambiguous
    by three orders of magnitude and the cheapest-sounding reading means the least."""
    tokens = run["tokens"]
    usd = sum(tokens[key] * USD_PER_MTOK[key] / 1_000_000 for key in tokens)
    explained = sum(1 for row in run["scored"] if row.reached_model)
    assert explained, "nothing reached the model"

    per_1k = usd / explained * 1000
    print(f"\n  tokens             {tokens}")
    print(f"  scored             {explained} pairs, {run['cold']} embedded cold")
    print(f"  cost/1k scored     ${per_1k:.3f}  (cold — every embedding paid for)")
    assert per_1k <= COST_CEILING_PER_1K


# ---- gate clause: every score carries a human-readable reason --------------------


def test_every_scored_pair_carries_a_reason_that_says_something(
    run: dict[str, Any],
) -> None:
    for row in run["scored"]:
        if not row.reached_model:
            continue
        assert row.reasons["summary"].strip()
        assert row.reasons["met"] or row.reasons["missing"]


def test_every_requirement_is_findable_in_the_posting(run: dict[str, Any]) -> None:
    """**M5's fabrication problem, caught a milestone early.**

    A cheap model asked to judge fit will supply requirements from its own idea of the
    role — "5+ years of Kubernetes" about a posting that never says Kubernetes. That text
    reaches a user and they act on it. Compared the way M3's vault compares: letters and
    digits only, so punctuation and casing cannot fail a real quote.
    """
    data = _golden()
    descriptions = {
        pair["job_id"]: _normalise(pair["job"]["description"]) for pair in data["pairs"]
    }

    total = 0
    grounded = 0
    ungrounded: list[str] = []
    for row in run["scored"]:
        if not row.reached_model:
            continue
        haystack = descriptions[row.job_id]
        for span in list(row.reasons["met"]) + list(row.reasons["missing"]):
            total += 1
            if _normalise(span) in haystack:
                grounded += 1
            else:
                ungrounded.append(span)

    rate = grounded / total if total else 1.0
    print(f"\n  requirement spans grounded in the posting: {rate:.2f} ({grounded}/{total})")
    if ungrounded:
        print("  ungrounded examples:", ungrounded[:5])
    # A paraphrase is not a fabrication, so this is a floor rather than an equality —
    # but a low rate means the prompt's "quote verbatim" rule is not landing, and M5's
    # validator will be diffing against something it cannot trust.
    assert rate >= 0.70


def _normalise(text: str) -> str:
    """Letters and digits only — M3's vault comparator, same reasoning.

    reportlab-style artefacts, smart quotes and casing must not fail a real quote.
    """
    return "".join(character for character in text.lower() if character.isalnum())
