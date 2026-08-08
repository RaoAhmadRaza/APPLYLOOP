"""The golden set's own invariants, checked without a model or a database.

These are cheap and they exist because a golden set rots quietly. Nothing here measures
the matcher — that is `test_matching_live.py`'s job. What these assert is that the *set*
still has the properties BAR.md committed to before anyone saw a score, because every one
of them is a property that a well-meaning edit removes without anyone noticing:

  * a pair labelled by the model under test measures self-consistency, not quality;
  * a matcher output leaking into the file anchors the next re-label;
  * the hard-negative quota decaying turns precision into a measurement of how many
    warehouse jobs are in the sample;
  * a stratum silently collapsing to "things the matcher already likes".
"""

import json
import pathlib
from typing import Any

import pytest

GOLDEN = pathlib.Path(__file__).parents[2] / "evals" / "golden"
PAIRS = GOLDEN / "pairs.json"
BAR = GOLDEN / "BAR.md"

# BAR.md §6. The labeller's vocabulary is deliberately not the product's.
LABELS = {"relevant", "not_relevant", "borderline"}

# Keys that must never appear on a pair. Any of them anchors a re-label on the matcher's
# own opinion, which is the leakage the stratified draw exists to prevent.
FORBIDDEN = {"score", "cosine", "similarity", "rank", "label_predicted", "good_fit"}

# BAR.md §7. The strata whose pairs reach the matcher — `filtered_out` and `pool_random`
# are drawn to produce structurally easy negatives, so the hard-negative ratio is scoped
# away from them.
JUDGED_STRATA = {"on_topic", "candidate_random"}


def _load() -> dict[str, Any]:
    if not PAIRS.exists():
        pytest.skip("evals/golden/pairs.json not built yet — run evals/golden/build_worksheet.py")
    return json.loads(PAIRS.read_text())


def _pairs() -> list[dict[str, Any]]:
    pairs: list[dict[str, Any]] = _load()["pairs"]
    return pairs


def _labelled() -> list[dict[str, Any]]:
    pairs = [pair for pair in _pairs() if pair["label"] is not None]
    if not pairs:
        pytest.skip("golden set is not labelled yet — see evals/golden/BAR.md §6")
    return pairs


# ---- the bar exists, and predates the labels -------------------------------------


def test_the_bar_is_committed() -> None:
    """CLAUDE.md §9 wants the bar "set in advance". A missing file is not a bar."""
    assert BAR.exists()
    text = BAR.read_text()
    for required in ("precision", "recall", "pool floor", "per-filter cap", "inconclusive"):
        assert required in text


def test_the_worksheet_names_the_bar_and_its_seed() -> None:
    """The seed is what stops the draw being re-rolled until it looks convenient."""
    meta = _load()["_meta"]
    assert meta["seed"]
    assert meta["bar"] == "evals/golden/BAR.md"


# ---- no matcher output may leak into the set -------------------------------------


def test_no_pair_carries_a_matcher_opinion() -> None:
    """Mechanical leakage protection.

    A stored score or rank turns the next re-labelling session into a review of the
    matcher's homework rather than an independent judgement.
    """
    for pair in _pairs():
        assert FORBIDDEN.isdisjoint(pair), f"{pair['job_id']} carries matcher output"


def test_every_pair_stores_its_job_payload() -> None:
    """A pair pinned only by id rots: `close_missing` or a dedupe promotion removes the
    job from the pool and precision changes with no code change."""
    for pair in _pairs():
        job = pair["job"]
        assert job["title"] and job["url"]
        assert "description" in job


# ---- the strata still mean what they meant ---------------------------------------


def test_every_pair_declares_its_stratum() -> None:
    strata = set(_load()["_meta"]["strata"])
    for pair in _pairs():
        assert pair["stratum"] in strata


def test_the_filtered_out_stratum_records_which_filter_dropped_it() -> None:
    """This one key is what makes "hard filters demonstrably drop mismatches" assertable
    per pair rather than in aggregate."""
    dropped = [pair for pair in _pairs() if pair["stratum"] == "filtered_out"]
    assert dropped, "no filtered_out pairs — the filter clause becomes unmeasurable"
    for pair in dropped:
        assert pair["expected_filter"] in {
            "location",
            "remote",
            "seniority",
            "work_auth",
            "keywords",
        }


def test_the_set_is_not_drawn_from_one_profile() -> None:
    """A set that collapses onto one résumé measures that résumé."""
    assert len({pair["profile"] for pair in _pairs()}) >= 3


# ---- the labels, once they exist -------------------------------------------------


def test_the_set_is_large_enough_to_report_on() -> None:
    """CLAUDE.md §9 says ~50 hand-labelled pairs."""
    assert len(_labelled()) >= 50


def test_every_label_uses_the_labellers_vocabulary_not_the_products() -> None:
    """`good_fit`/`fair`/`reach` here would conflate "should this person see it" with
    "where does the score fall", and make the score-to-label mapping unfalsifiable."""
    for pair in _labelled():
        assert pair["label"] in LABELS


def test_a_model_labelled_set_is_marked_proposed_and_cannot_be_confirmed() -> None:
    """**The one that matters most.**

    A set labelled by a model measures self-consistency, not quality. Model labels are
    still a legitimate *intermediate* state — pre-labelling and then having a human
    correct is far faster than judging fifty pairs cold, and it is what `_meta.status`
    exists to track.

    What must never happen is a proposed set being mistaken for a confirmed one. So the
    invariant is not "no model ever labels" but "a set containing a model label cannot
    be `confirmed`", and the live gate refuses to run on anything else.
    """
    status = _load()["_meta"].get("status", "confirmed")
    assert status in {"proposed", "confirmed"}

    model_labelled = [
        pair for pair in _labelled() if not str(pair["labelled_by"]).startswith("human:")
    ]
    if status == "confirmed":
        assert not model_labelled, (
            f"{len(model_labelled)} pairs are still model-labelled — a confirmed set "
            "must be human-reviewed end to end"
        )
    for pair in _labelled():
        assert str(pair["labelled_by"]).startswith(("human:", "model:"))


def test_a_confirmed_set_has_no_unlabelled_pairs() -> None:
    """The invariant that makes extending the set safe.

    Every consumer filters on `label is not None`, so an unlabelled pair does not fail —
    it silently shrinks the denominator. Add 80 rows to a `confirmed` set, label 40, and
    the gate reports precision over the half someone finished, with no indication that it
    is measuring half a set.

    So `confirmed` means *fully* labelled. The extension flow drops the set back to
    `proposed`, which this permits and the live gate refuses to run on.
    """
    if _load()["_meta"].get("status", "confirmed") != "confirmed":
        pytest.skip("golden set is 'proposed' — unlabelled pairs are expected mid-extension")
    unlabelled = [pair for pair in _pairs() if pair["label"] is None]
    assert not unlabelled, (
        f"{len(unlabelled)} pairs are unlabelled in a confirmed set — every consumer "
        "filters them out, so this shrinks the denominator rather than failing"
    )


def test_enough_of_the_negatives_are_hard() -> None:
    """BAR.md §7, as amended 2026-08-08. Two floors, because one number could not say it.

    A warehouse role against a backend profile is filler, not a negative — a matcher
    doing nothing but a keyword grep scores perfectly against those, so precision over
    them measures nothing. This is the assertion that stops the set decaying toward easy
    as pairs are added.

    The ratio is scoped to `on_topic` + `candidate_random` because those are the only
    strata whose pairs reach the matcher, and precision on hard negatives is computed
    over judged pairs only. The other two strata exist *to* produce easy negatives:
    `filtered_out` verifies a filter dropped something correctly, and `pool_random` is
    the uniform calibration draw that screams if a filter has emptied the pool. Holding
    them to a hard-negative quota penalises those strata for working.

    The absolute floor is what the ratio alone cannot express: a set with three negatives
    can satisfy any percentage.
    """
    if _load()["_meta"].get("status", "confirmed") != "confirmed":
        pytest.skip(
            "golden set is 'proposed'; the quota is a gate-readiness property and the "
            "live gate already refuses to run on anything but 'confirmed'"
        )
    negatives = [pair for pair in _labelled() if pair["label"] == "not_relevant"]
    if not negatives:
        pytest.skip("no negatives labelled yet")

    hard = [pair for pair in negatives if pair["negative_type"] == "hard"]
    assert len(hard) >= 10, f"{len(hard)} hard negatives overall; BAR.md §7 floors it at 10"

    judged = [pair for pair in negatives if pair["stratum"] in JUDGED_STRATA]
    assert judged, "no negatives in the strata that reach the matcher"
    judged_hard = [pair for pair in judged if pair["negative_type"] == "hard"]
    assert len(judged_hard) / len(judged) >= 0.6, (
        f"{len(judged_hard)}/{len(judged)} hard among judged strata — precision on hard "
        "negatives stops meaning anything below BAR.md §7's floor"
    )


def test_every_pair_declares_its_split() -> None:
    """BAR.md §3. An explicit field, because the split used to be positional.

    `scored[:20]` over a profile-grouped file is a profile split, not a random one: it put
    80% of one résumé in the tuning half and chose the threshold on that résumé's score
    distribution. Nothing caught it because the shape was only visible once the labels
    changed. A declared field cannot drift when rows are appended or reordered.
    """
    for pair in _pairs():
        assert pair["split"] in {"tune", "report"}


def test_each_split_carries_every_profile_and_enough_positives() -> None:
    """The property the positional split silently lost.

    `filtered_out` pairs never reach the model, so they cannot become predicted positives
    — the stratum is the offline proxy for reachability, checked here rather than live so
    a bad split fails in the cheap suite instead of after a paid run.

    §2 floors predicted positives on the *reporting* split at 8. The tuning split had no
    floor at all, which is how a threshold came to be chosen over two positives.
    """
    profiles = {pair["profile"] for pair in _labelled()}
    for split in ("tune", "report"):
        rows = [pair for pair in _labelled() if pair["split"] == split]
        assert {pair["profile"] for pair in rows} == profiles, (
            f"the {split} split is missing a profile — a threshold fitted on one résumé "
            "and applied to another is the failure BAR.md §3 exists to prevent"
        )
        reachable = [pair for pair in rows if pair["stratum"] != "filtered_out"]
        relevant = [pair for pair in reachable if pair["label"] == "relevant"]
        assert len(relevant) >= 5, f"{split} split has {len(relevant)} reachable positives"


def test_both_classes_are_represented() -> None:
    """Precision is undefined without positives and meaningless without negatives."""
    labels = [pair["label"] for pair in _labelled()]
    assert labels.count("relevant") >= 10
    assert labels.count("not_relevant") >= 10
