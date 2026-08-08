"""From the model's partition to a score, a label and a reason. Pure, no network.

The `profiles/derive.py` analogue, and here for a sharper reason than symmetry: Part 14
says the threshold is set empirically by the golden set, and a threshold only means
something against a stable score distribution. The model reports which stated
requirements the profile evidences; this file turns that into a number the same way
every time, so a re-run over an unchanged pair produces an unchanged score and the
golden set measures the matcher rather than the weather.

It also means the arithmetic is testable with no model calls at all, which keeps the
paid live gate pointed at the one thing only a live model can answer.
"""

from schemas.enums import MatchLabel
from schemas.match import MatchFacts, MatchReasons

# One band up is a stretch, not a mismatch — the filter already dropped anything further
# away. This is the only place the label vocabulary distinguishes "you could get this"
# from "you are a fit", and it is a direction rather than a distance for that reason.
_REACH_DELTA = 1


def coverage(facts: MatchFacts) -> float | None:
    """Share of the posting's stated requirements the profile evidences.

    None when the posting stated none. That is not zero coverage — a posting with no
    requirements has told us nothing, and scoring it 0 would rank it below a genuine bad
    fit. Same polarity as every other unknown in this repo.
    """
    stated = len(facts.met) + len(facts.missing)
    if stated == 0:
        return None
    return len(facts.met) / stated


def score(facts: MatchFacts) -> int | None:
    """0–100, or None when the posting stated no requirements.

    Deliberately one term. Cosine similarity is **not** folded in: it decides who gets
    asked, not how good the answer is, and a second weight would be fitted to fifty
    hand-labelled pairs — which is over-fitting with extra steps.

    **A disqualifier is a gate, not a term.** Coverage is a ratio, and a ratio cannot
    express "fatal": a posting that adds "ITAR: must be a U.S. person" to fifteen matched
    bullets scores 94 while being a role the candidate legally cannot hold. That
    arithmetic is what failed M4's first gate at precision 0.50–0.62 — 7 of 8 false
    positives were right-craft, right-band postings differing on exactly one dimension.
    Weighting the term instead of gating it would just move the number a candidate needs
    to overcome; there is no coverage high enough to make an illegal application good.
    """
    if facts.disqualifiers:
        # 0, not None. None already means "the posting stated no requirements", and a
        # rejection that reads as "nothing to go on" would put this job back in front of
        # anything sorting nulls last.
        return 0
    share = coverage(facts)
    return None if share is None else round(100 * share)


def label(value: int | None, *, threshold: int, seniority_delta: int | None) -> MatchLabel | None:
    """Good fit, fair, or reach.

    `threshold` is an argument and never a module constant. Part 14 forbids a magic
    number here until the golden set produces one, and a default would be that invention
    wearing a keyword argument.
    """
    if value is None:
        return None
    if value >= threshold:
        return MatchLabel.GOOD_FIT
    if seniority_delta is not None and seniority_delta >= _REACH_DELTA:
        # Below the bar because the role is more senior, not because the fit is poor.
        return MatchLabel.REACH
    return MatchLabel.FAIR


def reasons(
    facts: MatchFacts,
    *,
    similarity: float,
    seniority_delta: int | None,
    filters_passed: list[str],
    threshold: int,
    model: str,
    embed_model: str,
) -> dict[str, object]:
    """The `reasons_json` payload — the gate clause "every score carries a reason".

    Everything that went into the number, including the two things that did not: the
    similarity that chose the shortlist and the threshold the score was compared against.
    A reason a reader cannot check against the posting is not a reason.
    """
    return MatchReasons(
        summary=facts.summary,
        met=facts.met,
        missing=facts.missing,
        disqualifiers=facts.disqualifiers,
        coverage=coverage(facts),
        similarity=similarity,
        seniority_delta=seniority_delta,
        filters_passed=filters_passed,
        threshold=threshold,
        model=model,
        embed_model=embed_model,
    ).model_dump(mode="json")
