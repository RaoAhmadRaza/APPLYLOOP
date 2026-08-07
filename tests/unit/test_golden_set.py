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


def test_no_counted_pair_was_labelled_by_a_model() -> None:
    """**The one that matters most.**

    A set labelled by the thing under test measures self-consistency. If a model ever
    pre-sorts candidates for human review it must be from a different family, and the
    human still decides — so the recorded labeller is always a person.
    """
    for pair in _labelled():
        labeller = pair["labelled_by"]
        assert isinstance(labeller, str) and labeller.startswith("human:"), (
            f"{pair['job_id']} was labelled by {labeller!r}"
        )


def test_enough_of_the_negatives_are_hard() -> None:
    """BAR.md §7: at least 60%.

    A warehouse role against a backend profile is filler, not a negative — a matcher
    doing nothing but a keyword grep scores perfectly against those, so precision over
    them measures nothing. This is the assertion that stops the set decaying toward easy
    as pairs are added.
    """
    negatives = [pair for pair in _labelled() if pair["label"] == "not_relevant"]
    if not negatives:
        pytest.skip("no negatives labelled yet")
    hard = [pair for pair in negatives if pair["negative_type"] == "hard"]
    assert len(hard) / len(negatives) >= 0.6


def test_both_classes_are_represented() -> None:
    """Precision is undefined without positives and meaningless without negatives."""
    labels = [pair["label"] for pair in _labelled()]
    assert labels.count("relevant") >= 10
    assert labels.count("not_relevant") >= 10
