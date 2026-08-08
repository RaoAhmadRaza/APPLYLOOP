"""The arithmetic, with no model in sight.

That is the point of splitting it out: everything here is what the score *is*, and none
of it needs a paid call to test. What a live model can uniquely answer — whether the
partition it produced is any good — is the golden set's job, in
`tests/integration/test_matching_live.py`.
"""

import pytest
from schemas.enums import MatchLabel
from schemas.match import MatchFacts
from workers.matching import score


def _facts(met: int, missing: int, summary: str = "Strong on Python, no Kubernetes.") -> MatchFacts:
    return MatchFacts(
        met=[f"requirement {n}" for n in range(met)],
        missing=[f"gap {n}" for n in range(missing)],
        summary=summary,
    )


# ---- coverage and score ----------------------------------------------------------


@pytest.mark.parametrize(
    ("met", "missing", "expected"),
    [(4, 0, 100), (3, 1, 75), (1, 1, 50), (1, 3, 25), (0, 4, 0), (2, 1, 67)],
)
def test_the_score_is_the_share_of_stated_requirements_met(
    met: int, missing: int, expected: int
) -> None:
    assert score.score(_facts(met, missing)) == expected


def test_a_posting_that_states_no_requirements_scores_unknown_not_zero() -> None:
    """Zero would rank a vague posting below a genuine bad fit.

    Same polarity as every other unknown here: `remote_mode IS NULL`, an unbanded title,
    a résumé that names no city. The stage writes NULL and skips it rather than guessing.
    """
    assert score.score(_facts(0, 0)) is None
    assert score.coverage(_facts(0, 0)) is None


def test_the_score_is_stable_across_repeated_calls() -> None:
    """The whole reason the model does not emit the number.

    A threshold is calibrated against a distribution. If the same pair scored 85 and then
    82, the golden set would be measuring the model's mood and M5's above-threshold set
    would flicker under it between runs.
    """
    facts = _facts(3, 2)

    assert len({score.score(facts) for _ in range(10)}) == 1


def test_the_score_stays_inside_the_column_constraint() -> None:
    """`matches.score` carries a CHECK of 0..100. A score outside it is an insert error
    at the end of a paid run rather than a caught mistake."""
    for met in range(6):
        for missing in range(6):
            value = score.score(_facts(met, missing))
            assert value is None or 0 <= value <= 100


# ---- the label -------------------------------------------------------------------


def test_at_or_above_the_threshold_is_a_good_fit() -> None:
    assert score.label(80, threshold=80, seniority_delta=0) == MatchLabel.GOOD_FIT
    assert score.label(81, threshold=80, seniority_delta=0) == MatchLabel.GOOD_FIT


def test_below_the_threshold_on_a_more_senior_role_is_a_reach() -> None:
    """Below the bar because the role is a step up, not because the fit is poor."""
    assert score.label(60, threshold=80, seniority_delta=1) == MatchLabel.REACH


def test_below_the_threshold_at_the_same_level_is_fair() -> None:
    assert score.label(60, threshold=80, seniority_delta=0) == MatchLabel.FAIR


def test_a_more_junior_role_is_never_a_reach() -> None:
    """Reach is a direction, not a distance. A director posting scored low against a
    junior profile is a reach; a junior posting scored low against a director is not."""
    assert score.label(60, threshold=80, seniority_delta=-1) == MatchLabel.FAIR


def test_an_unknown_seniority_delta_is_fair_not_a_reach() -> None:
    """Two thirds of postings state no band. Calling all of them reaches would make the
    label meaningless on the majority of the pool."""
    assert score.label(60, threshold=80, seniority_delta=None) == MatchLabel.FAIR


def test_an_unknown_score_has_no_label() -> None:
    assert score.label(None, threshold=80, seniority_delta=1) is None


def test_the_threshold_has_no_default() -> None:
    """Part 14 forbids a magic number here until the golden set produces one, and a
    default would be exactly that invention wearing a keyword argument.

    Asserted rather than trusted, because adding one is a one-character change.
    """
    with pytest.raises(TypeError):
        score.label(60, seniority_delta=0)  # type: ignore[call-arg]


# ---- reasons ---------------------------------------------------------------------


def test_the_reason_carries_everything_that_went_into_the_number() -> None:
    """The gate clause is "every score carries a human-readable reason", and a reason a
    reader cannot check against the posting is not one."""
    payload = score.reasons(
        _facts(2, 1),
        similarity=0.71,
        seniority_delta=1,
        filters_passed=["location", "remote"],
        threshold=80,
        model="test/model",
        embed_model="test/embed@v1",
    )

    assert payload["summary"] == "Strong on Python, no Kubernetes."
    assert payload["met"] == ["requirement 0", "requirement 1"]
    assert payload["missing"] == ["gap 0"]
    assert payload["coverage"] == pytest.approx(2 / 3)
    assert payload["similarity"] == 0.71
    assert payload["threshold"] == 80
    assert payload["model"] == "test/model"
    assert payload["embed_model"] == "test/embed@v1"


def test_the_reason_is_json_serialisable_for_a_jsonb_column() -> None:
    """`reasons_json` is JSONB. A pydantic object or a float32 in there fails at insert,
    at the end of a run that already spent the money."""
    import json

    json.dumps(
        score.reasons(
            _facts(1, 1),
            similarity=0.5,
            seniority_delta=None,
            filters_passed=[],
            threshold=70,
            model="m",
            embed_model="e",
        )
    )


def test_similarity_is_recorded_but_is_not_in_the_score() -> None:
    """§7.2's complaint about cosine is that it produces plausible-but-wrong matches.

    It chooses who gets asked; it must not also decide how good the answer was, or the
    golden set has two weights to calibrate against fifty pairs.
    """
    facts = _facts(3, 1)
    low = score.reasons(
        facts,
        similarity=0.1,
        seniority_delta=0,
        filters_passed=[],
        threshold=80,
        model="m",
        embed_model="e",
    )
    high = score.reasons(
        facts,
        similarity=0.99,
        seniority_delta=0,
        filters_passed=[],
        threshold=80,
        model="m",
        embed_model="e",
    )

    assert low["coverage"] == high["coverage"]
    assert score.score(facts) == 75
    assert low["similarity"] != high["similarity"]


# ---- disqualifiers: a gate, not a term ------------------------------------------


def test_a_bar_zeroes_a_score_that_coverage_would_have_made_excellent() -> None:
    """**The defect that failed M4's first live gate.**

    Coverage is a ratio, so "open only to current university students" costs one bullet out
    of fifteen and a role the candidate cannot hold scores 94. 7 of 8 false positives were
    exactly this shape: right craft, right band, one fatal clause.
    """
    facts = MatchFacts(
        met=[f"requirement {index}" for index in range(14)],
        missing=[],
        disqualifiers=[],
        summary="Strong match on the engineering requirements.",
    )

    assert score.coverage(facts) == 1.0
    assert score.score(facts, ["eligibility: open only to current university students"]) == 0


def test_a_bar_scores_zero_rather_than_none() -> None:
    """`None` already means "the posting stated no requirements" — nothing to go on.

    A rejection that arrives as None would sort with the unknowns rather than the
    rejects, and anything ordering nulls last puts an illegal application back on top.
    """
    facts = MatchFacts(met=[], missing=[], disqualifiers=[], summary="")

    assert score.score(facts, ["work authorisation: the posting is scoped to the US"]) is not None
    assert score.score(facts, ["work authorisation: the posting is scoped to the US"]) == 0


def test_a_model_quoted_disqualifier_is_advisory_and_does_not_touch_the_score() -> None:
    """**Measured, not preferred.**

    On the 121-pair gate run `bars.py` produced 10 correct rejections and 0 spurious; the
    model produced 6 correct and ~11 spurious — a pay disclosure, a `To apply:` URL, a
    timezone window the candidate is inside, a sponsorship refusal for someone needing
    none, and twice the candidate's own résumé sentence. Each spurious one deletes a job
    the user could have had, and recall pays for it. It still reaches `reasons_json`, so
    M6 can show a human why the model was uneasy — see the reason test below.
    """
    facts = MatchFacts(
        met=["Python", "Postgres", "Celery"],
        missing=["Kubernetes"],
        disqualifiers=["Compensation: $180,000 - $220,000"],
        summary="Strong match.",
    )

    assert score.score(facts) == 75
    assert score.score(facts, []) == 75


def test_an_empty_disqualifier_list_leaves_the_coverage_path_untouched() -> None:
    """The regression direction. Every existing score must be what it was."""
    facts = MatchFacts(met=["a", "b", "c"], missing=["d"], disqualifiers=[], summary="")

    assert score.score(facts) == 75
    assert score.coverage(facts) == 0.75


def test_the_reason_carries_the_disqualifier_in_the_postings_own_words() -> None:
    """ "You scored 0" is not a reason, and M6 puts this in front of a human."""
    facts = MatchFacts(
        met=["Python"],
        missing=[],
        disqualifiers=["Must be a US Citizen (no visa sponsorship available)"],
        summary="Blocked on work authorisation.",
    )

    payload = score.reasons(
        facts,
        similarity=0.9,
        seniority_delta=0,
        filters_passed=[],
        threshold=80,
        model="m",
        embed_model="e",
    )

    assert payload["disqualifiers"] == ["Must be a US Citizen (no visa sponsorship available)"]
