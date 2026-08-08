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
from workers.matching import bars, embed, filters, prompt, score

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
# POOL_FLOOR and PER_FILTER_CAP live in `test_filters_live.py` — they are properties of
# the filters against the real pool, and unmeasurable against seeded payloads.
COST_CEILING_PER_1K = 2.00

# BAR.md §5. Beside the model names so they cannot go stale unnoticed.
USD_PER_MTOK = {"embed": 0.02, "prompt": 0.05, "completion": 0.40}

# BAR.md §3. Read from each pair's `split` field, never re-derived from position.
#
# It used to be `scored[:20]`, and that was wrong in a way nothing caught: `scored` is
# built by iterating fixtures, and pairs.json is grouped by profile, so a positional cut
# is a *profile* cut. The tuning split was 80% one résumé, which chose the threshold on
# one profile's score distribution and applied it to three others — overfitting by
# construction, through a door §3's "lowest, not best" rule does not cover.
MIN_TUNE_POSITIVES = 5

# BAR.md §3's stability rule. The chosen threshold must clear every bar on this many
# consecutive runs. Default 1 so iterating on the prompt costs one pass; the gate itself
# is run with 3, which is what `make verify-live-match` sets.
MATCH_RUNS = int(os.getenv("APPLYLOOP_MATCH_RUNS", "1"))


@dataclass(frozen=True)
class Scored:
    profile: str
    job_id: str
    # For the printouts, because a job id cannot identify a pair here. Every golden job was
    # drawn in the same instant and the ids are UUIDv7, so the first 8 characters are a
    # timestamp: one prefix in the last run covered 24 different pairs. A diagnosis printed
    # against those is unreadable.
    title: str
    label: str
    stratum: str
    expected_filter: str | None
    negative_type: str | None
    split: str
    reached_model: bool
    score: int | None
    reasons: dict[str, Any]


def _golden() -> dict[str, Any]:
    return json.loads(PAIRS.read_text())


def _labelled(data: dict[str, Any]) -> list[dict[str, Any]]:
    """The pairs the gate is allowed to count.

    A `proposed` set — one a model pre-labelled for a human to review — is refused
    outright rather than counted. Letting one through would make the gate a measurement
    of whether two models agree with each other, which is exactly the circularity the
    golden set exists to break.
    """
    status = data["_meta"].get("status", "confirmed")
    if status != "confirmed":
        pytest.skip(
            "golden set is marked 'proposed' — a human must review the labels and set "
            "_meta.status to 'confirmed' before this gate means anything"
        )
    pairs = [pair for pair in data["pairs"] if pair["label"] is not None]
    if len(pairs) < 50:
        pytest.skip(f"golden set has {len(pairs)} labelled pairs; needs 50 (BAR.md §2)")
    return pairs


@pytest.fixture(scope="session")
def runs(engine: Engine) -> list[dict[str, Any]]:
    """Score the whole golden set `MATCH_RUNS` times.

    More than one because **the gate is not deterministic**: two consecutive passes over
    an identical set and unchanged code gave precision 0.62 and 0.50 at the same
    threshold. `score()` is pure, but `met`/`missing` come from a model, so the score
    inherits the model's variance and a threshold pinned from a single run is pinned to
    noise. BAR.md §3 requires the chosen threshold to clear on every run.
    """
    return [_score_once(engine) for _ in range(MATCH_RUNS)]


@pytest.fixture(scope="session")
def run(runs: list[dict[str, Any]]) -> dict[str, Any]:
    """The first pass. Everything that is not the threshold reads this.

    Filter recall, grounding, cost and the funnel are properties of one pass; running
    them three times would triple the spend to re-measure the same thing.
    """
    return runs[0]


def _score_once(engine: Engine) -> dict[str, Any]:
    """One full pass over the golden set, for real.

    Opens its own session and rolls back, so the eval never leaves rows behind — and each
    golden profile gets its **own** `User`, because `matches` is keyed `(user_id, job_id)`
    with no `profile_id` and without that an eval run would write into the same rows as
    the real matcher.
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

            # The funnel over the **seeded** pool — this profile's stored payloads, not
            # production. It attributes which filter dropped each pair, which is what the
            # per-pair `expected_filter` assertions need. It is NOT a measurement of the
            # filters against the real pool; that is `test_filters_live.py`.
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
                    blocked = bars.check(job, view, profile.master_resume or "")
                    value = score.score(facts, blocked)
                    reasons = score.reasons(
                        facts,
                        bars=blocked,
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
                        title=pair["job"]["title"],
                        label=pair["label"],
                        stratum=pair["stratum"],
                        expected_filter=pair["expected_filter"],
                        negative_type=pair["negative_type"],
                        split=pair["split"],
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
        # Without this, `bars._country_scope` sees None on every profile and never fires
        # — the largest of the three deterministic bars, silently inert. It was, for one
        # whole gate run: the measured 0.42 -> 0.57 came from language and eligibility
        # alone. A seeded fixture that omits a column tests the code around it.
        work_auth_regions=blob.get("work_auth_regions"),
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


def _measurable(scored: list[Scored]) -> list[Scored]:
    """Drop profiles that contribute no `relevant` pair — BAR.md §2, amended 2026-08-08.

    A profile with no positives has no recall to measure and an unbounded precision
    denominator: every pair of theirs can only ever subtract. Including one does not make
    the bar harder in a way that means anything, it makes precision a function of how many
    all-negative profiles the draw happened to produce.

    **This is a rule about the draw, not a list of names.** `career_changer.docx` is
    excluded today because its 23 pairs hold zero positives; redraw it against jobs it can
    actually match and it re-enters on the next run with no edit here. That is deliberate —
    a hardcoded exclusion is how "this profile is failing" quietly becomes "this profile is
    exempt".

    Applied per split, because recall on a split is over that split's positives.
    """
    with_positives = {row.profile for row in scored if row.label == "relevant"}
    return [row for row in scored if row.profile in with_positives]


def _excluded(scored: list[Scored]) -> set[str]:
    """Who `_measurable` dropped. Printed, never silent — a coverage cap nobody reports
    reads exactly like coverage."""
    return {row.profile for row in scored} - {row.profile for row in _measurable(scored)}


def _sweep(scored: list[Scored]) -> list[tuple[int, float, float, int]]:
    """(threshold, precision, recall, predicted positives) for every candidate cut.

    Free, because every pair is already scored. This is the whole reason the score is a
    deterministic function of the model's facts rather than a number the model emits.
    """
    reachable = [
        row for row in _measurable(scored) if row.reached_model and row.label != "borderline"
    ]
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
    tune = [row for row in scored if row.split == "tune"]
    for row in _sweep(tune):
        if row[1] >= BAR_PRECISION and row[2] >= BAR_RECALL and row[3] >= MIN_TUNE_POSITIVES:
            return row
    return None


# ---- gate clause: precision meets the bar set in advance -------------------------


def test_a_threshold_clears_both_the_precision_bar_and_the_recall_floor(
    runs: list[dict[str, Any]],
) -> None:
    """The headline.

    The recall floor is not decoration: without it, a threshold of 100 labels nothing a
    good fit, precision is 1.0 by vacuity, and a matcher that matches nothing passes.

    The threshold is chosen **once**, by §3's procedure, on the first run's tuning split.
    It is then applied unchanged to every run's reporting split and must clear on all of
    them. Re-choosing per run would let each pass pick whichever cut happened to suit it,
    which is the overfitting §3 forbids wearing a stability rule as a disguise.
    """
    scored: list[Scored] = runs[0]["scored"]
    for fixture in sorted(_excluded(scored)):
        pairs = sum(1 for row in scored if row.profile == fixture)
        print(f"\n  EXCLUDED FROM METRICS  {fixture}  ({pairs} pairs, none labelled relevant)")
    picked = _chosen(scored)
    if picked is None:
        # A gate that fails without saying which bar it missed sends the next session
        # guessing, and the three candidate causes — precision, the recall floor, the
        # tuning-split positive floor — want completely different fixes.
        tune = [row for row in scored if row.split == "tune"]
        print("\n  tuning sweep (threshold, precision, recall, n):")
        for row in _sweep(tune):
            blocked = [
                name
                for name, ok in (
                    ("precision", row[1] >= BAR_PRECISION),
                    ("recall", row[2] >= BAR_RECALL),
                    ("n", row[3] >= MIN_TUNE_POSITIVES),
                )
                if not ok
            ]
            print(f"    {row[0]:3}  p={row[1]:.2f}  r={row[2]:.2f}  n={row[3]:2}  fails: {blocked}")

        # Which pairs the matcher called a fit and a human did not. "Precision is 0.62"
        # is a number; this is the thing that tells the next session what to change.
        clearing = [row for row in _sweep(tune) if row[2] >= BAR_RECALL]
        if clearing:
            cut = max(clearing, key=lambda row: row[1])[0]
            print(f"\n  false positives at threshold {cut}:")
            for pair in _measurable(tune):
                above = pair.reached_model and pair.score is not None and pair.score >= cut
                if above and pair.label == "not_relevant":
                    kind = pair.negative_type
                    print(f"    {pair.score:3}  [{kind}]  {pair.profile}  {pair.title[:60]}")
    assert picked is not None, (
        "no threshold clears both bars on the tuning split. BAR.md §3: the gate fails "
        "here, and is not rescued by editing the bar."
    )

    threshold = picked[0]
    print(f"\n  threshold          {threshold}   (chosen on run 1's tuning split)")
    print(f"  tuning  precision  {picked[1]:.2f}  recall {picked[2]:.2f}  n={picked[3]}")

    # **Printed whether the gate passes or fails, and printed for the reporting split.**
    # A run that clears on tune and misses on report says nothing about *which* of the two
    # is at fault: §3 takes the lowest threshold clearing both bars, which is by
    # construction the point of least margin, so a tuning 0.81 landing at 0.70 could be the
    # selection rule or could be the matcher. Without this the difference is invisible and
    # the next session's only way to look is another paid run. It is a diagnostic — the
    # threshold is still chosen by §3 on the tuning split alone, above, before this runs.
    print("\n  reporting-split sweep, run 1 (diagnostic — NOT how the threshold is chosen):")
    for row in _sweep([r for r in runs[0]["scored"] if r.split == "report"]):
        mark = "  <- chosen" if row[0] == threshold else ""
        clears = "PASS" if row[1] >= BAR_PRECISION and row[2] >= BAR_RECALL else "    "
        print(f"    {row[0]:3}  p={row[1]:.2f}  r={row[2]:.2f}  n={row[3]:2}  {clears}{mark}")

    # Every run's reporting split, at that one threshold. Collected before asserting so a
    # failure shows the whole spread rather than stopping at the first bad pass — the
    # spread is the measurement, and one number without it is what this rule exists to
    # stop anyone pinning.
    results = []
    for index, single in enumerate(runs, start=1):
        report = [row for row in single["scored"] if row.split == "report"]
        rows = [row for row in _sweep(report) if row[0] == threshold]
        if not rows:
            results.append((index, 0.0, 0.0, 0))
            continue
        _, precision, recall, positives = rows[0]
        results.append((index, precision, recall, positives))

    for index, precision, recall, positives in results:
        print(f"  report  run {index}    p={precision:.2f}  r={recall:.2f}  n={positives}")
    if len(results) > 1:
        spread = max(row[1] for row in results) - min(row[1] for row in results)
        print(f"  precision spread   {spread:.2f} across {len(results)} runs")
    print(f"\n  MATCH_THRESHOLD={threshold}")

    # The sweep above says whether a different cut would have cleared; this says what to
    # change if none would. Same reasoning as the tuning-split list, on the split that
    # actually decides the gate.
    if any(precision < BAR_PRECISION for _, precision, _, _ in results):
        print(f"\n  reporting-split false positives at threshold {threshold}, run 1:")
        for pair in _measurable([r for r in runs[0]["scored"] if r.split == "report"]):
            above = pair.reached_model and pair.score is not None and pair.score >= threshold
            if above and pair.label == "not_relevant":
                print(
                    f"    {pair.score:3}  [{pair.negative_type}]  {pair.profile}  {pair.title[:55]}"
                )

    for index, precision, recall, positives in results:
        assert positives >= MIN_PREDICTED_POSITIVES, (
            f"run {index}: {positives} predicted positives on the reporting split — "
            "BAR.md §2 calls this inconclusive, not green"
        )
        assert precision >= BAR_PRECISION, f"run {index}: precision {precision:.2f}"
        assert recall >= BAR_RECALL, f"run {index}: recall {recall:.2f}"


def test_precision_on_hard_negatives_is_reported(run: dict[str, Any]) -> None:
    """The honest headline. Precision over easy negatives measures how many warehouse
    jobs are in the sample, not how good the matcher is."""
    scored: list[Scored] = run["scored"]
    picked = _chosen(scored)
    assert picked is not None
    hard = [
        row
        for row in _measurable(scored)
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


def test_the_funnel_is_reported(run: dict[str, Any]) -> None:
    """Print where each profile's pool went, and assert only what is measurable here.

    **The pool floor and the per-filter cap moved to `test_filters_live.py`.** They cannot
    be asserted in this harness: §7 requires a golden run to seed only the stored payloads,
    so the pool is one profile's pairs — 16 rows — and a floor of 200 is a failure the
    matcher cannot fix. Asserting them here reported a defect that did not exist while
    saying nothing about the filters against the pool that actually bills. See BAR.md §8.
    """
    for fixture, funnel in run["funnels"].items():
        print(f"\n  {fixture}: {funnel}")
        assert (
            funnel["pool"]
            == sum(funnel[key] for key in funnel if key.startswith("dropped_"))
            + funnel["candidates"]
            + funnel["already_scored"]
        ), f"{fixture}: the funnel does not sum to the pool — a job was counted twice"


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
        spans = (
            list(row.reasons["met"])
            + list(row.reasons["missing"])
            # A fabricated disqualifier is the worst span this stage can emit: it does not
            # merely mislead, it deletes the job from the user's results outright.
            + list(row.reasons["disqualifiers"])
        )
        for span in spans:
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


# ---- prompt quality: the instruction the labelling model kept failing ------------


def test_a_pair_with_a_stated_bar_is_rejected(run: dict[str, Any]) -> None:
    """**The outcome, not the mechanism.**

    `disqualifier_expected` is set only on pairs whose posting states a bar the profile
    fails, detected by searching the stored description rather than by judgement. Two
    things can reject them — the model quoting the clause, or `bars.py` computing it — and
    which one fires is an implementation detail that has already changed once.

    An earlier version asserted the model specifically, and it failed on a pair the
    coverage ratio had correctly scored 0: a correct outcome reported as a defect. Then it
    passed 0/0, because the deterministic layer rejected every flagged pair before
    extraction mattered — an empty population asserting nothing at all. Both are the same
    mistake, which is testing how rather than what.

    What matters is that none of these becomes a false positive. The per-mechanism counts
    are printed, because a swing there is worth seeing even when the property holds.
    """
    data = _golden()
    expected = {pair["job_id"] for pair in data["pairs"] if pair.get("disqualifier_expected")}
    assert expected, "no pair carries disqualifier_expected — this test proves nothing"

    by_model = 0
    by_bars = 0
    survived: list[str] = []
    checked = 0
    for row in run["scored"]:
        if not row.reached_model or row.job_id not in expected:
            continue
        checked += 1
        if row.reasons["disqualifiers"]:
            by_model += 1
        if row.reasons["bars"]:
            by_bars += 1
        if row.score != 0:
            survived.append(f"{row.profile} {row.title[:60]!r} scored {row.score}")

    print(f"\n  pairs with a stated bar: {checked}, rejected {checked - len(survived)}")
    print(f"    quoted by the model: {by_model}   computed by bars.py: {by_bars}")
    for line in survived:
        print(f"    SURVIVED  {line}")
    assert checked, "no flagged pair reached the model — the filters took them all"
    assert not survived, (
        f"{len(survived)} of {checked} postings state a bar the profile fails, and "
        "nothing rejected them; these are exactly the false positives precision is lost to"
    )


def test_a_disqualifier_is_relative_to_the_profile_not_the_posting(run: dict[str, Any]) -> None:
    """The control that stops the fix becoming a keyword blocklist.

    Both Proxify postings say "unable to consider applications from candidates in other
    time zones". For the Portland profile that is fatal; for the Kraków profile, which
    sits inside CET, it is not — and `two_column.pdf` labels one of them `relevant`.
    A rule that fired on the sentence rather than on the pair would destroy recall while
    looking like it fixed precision.
    """
    # Collected, not asserted in the loop. A rejected positive costs recall directly, and
    # stopping at the first one hides how many there are and whether they share a cause —
    # which is the whole question when recall falls off a cliff.
    rejected = [
        (row, row.reasons["disqualifiers"] + row.reasons["bars"])
        for row in run["scored"]
        if row.reached_model and row.label == "relevant" and row.score == 0
    ]
    for row, why in rejected:
        print(f"\n  REJECTED POSITIVE  {row.profile} {row.title[:60]!r}  {why}")
    assert not rejected, (
        f"{len(rejected)} pairs labelled relevant were rejected outright. Every one is a "
        "job the user would have wanted, and recall pays for each."
    )
