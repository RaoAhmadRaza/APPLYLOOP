"""Reading a seniority band off a job title, and comparing two bands.

Worker infrastructure, alongside `llm.py` and `settings.py` — deliberately **not** inside
a stage package. M3 bands a résumé's current title to fill `profiles.seniority`; M4 bands
`jobs.title` to decide whether a posting is within reach, and §3.1 forbids
`workers/matching/` importing `workers/profiles/`. The bands were in `profiles/derive.py`
until M4 needed them; same move `db.events.record` made for the same reason.

`RANK` is new here rather than moved, and it exists because **neither ordering already in
the codebase is a rank**:

  * `Seniority`'s declaration order puts LEAD and DIRECTOR *after* PRINCIPAL, so
    `list(Seniority).index()` says a lead outranks a principal.
  * `TITLE_BANDS` below is a *match-precedence* order — most specific keyword first, so
    "Senior Staff Engineer" resolves to STAFF — and it puts INTERN above JUNIOR.

`TITLE_BANDS` is public because M4 needs the keywords themselves, not just the verdict:
its seniority filter drops a posting whose title states a band outside the candidate's
window, and rebuilding that keyword list as a SQL `CASE` would be the second copy this
module exists to prevent.

Both look like rank if you squint, which is exactly why a comparison written against
either would be wrong in a way no test notices. Pinned by `tests/unit/test_seniority.py`.
"""

import re

from schemas.enums import Seniority

# Title keywords, most senior first — the first match wins, so "Senior Staff Engineer"
# resolves to STAFF rather than SENIOR. Word-boundary matched, or "principal" would fire
# on "principally".
TITLE_BANDS: list[tuple[Seniority, tuple[str, ...]]] = [
    (Seniority.DIRECTOR, ("director", "vp", "vice president", "head of")),
    (Seniority.PRINCIPAL, ("principal", "distinguished", "fellow")),
    (Seniority.STAFF, ("staff", "architect")),
    (Seniority.LEAD, ("lead", "manager")),
    (Seniority.SENIOR, ("senior", "sr")),
    (Seniority.INTERN, ("intern", "internship", "trainee", "apprentice")),
    (Seniority.JUNIOR, ("junior", "jr", "associate", "graduate", "entry level")),
]

# The comparison order, which is not the declaration order and not the match order.
# LEAD and STAFF deliberately share a rank: they are peers on the management and
# individual-contributor tracks, and treating one as senior to the other would drop
# genuine matches in whichever direction we guessed wrong.
RANK: dict[Seniority, int] = {
    Seniority.INTERN: 0,
    Seniority.JUNIOR: 1,
    Seniority.MID: 2,
    Seniority.SENIOR: 3,
    Seniority.LEAD: 4,
    Seniority.STAFF: 4,
    Seniority.PRINCIPAL: 5,
    Seniority.DIRECTOR: 6,
}


def band(title: str | None) -> Seniority | None:
    """The band a title states, or None when it states nothing.

    None is the common case on real postings — most titles are "Software Engineer" with
    no adjective — and it must never be read as a failed filter. Same polarity as
    `jobs.remote_mode IS NULL`: the source did not say, so we do not know.
    """
    if not title:
        return None
    lowered = title.lower()
    for level, keywords in TITLE_BANDS:
        if any(re.search(rf"\b{re.escape(word)}\b", lowered) for word in keywords):
            return level
    return None


def distance(a: Seniority | None, b: Seniority | None) -> int | None:
    """How many bands apart, or None when either side is unknown.

    Signed from `a`'s point of view: positive means `b` is more senior. Callers that
    only care how far apart take `abs()`; M4's label needs the direction, because a job
    one band *above* the candidate is a REACH and one band below is not.
    """
    if a is None or b is None:
        return None
    return RANK[b] - RANK[a]
