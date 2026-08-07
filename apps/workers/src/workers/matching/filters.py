"""§3.5's free rung: the deduped pool down to the jobs worth spending money on.

One SELECT, no network, no model, commits nothing. Part 13 rule 6 forbids calling an LLM
on a job a `WHERE` clause could have dropped, so everything expressible in SQL happens
here and the result is a list of ids nothing downstream can widen.

**Every filter passes when either side is silent.** Written five times below because it
is wrong five different ways, and each one fails the same silent way: the run returns
few or no candidates, precision over the survivors looks excellent, and the product
shows the user nothing. Measured against the real pool on 2026-08-07:

    jobs.remote_mode IS NULL        11,957 / 13,725  (87%)
    a title stating no band          ~66% of postings
    a description stating no
      work-auth requirement          the overwhelming majority

Reading any of those as a failed filter drops most of the pool. DECISIONS.md's
`is_remote=False → NULL, never ONSITE` entry set this polarity at M2; this file is the
same rule applied where it costs the most.

**Location is coarse on purpose, and this is a change from the shape §3.5 implies.**
`profiles.locations` holds `"City, Region"` as the résumé spelled it and `jobs.locations`
holds each source's own strings, so the array overlap the schema was built for matches
almost nothing — measured, on the open pool:

    profiles.locations && jobs.locations   "Portland, OR" -> 4     "San Francisco, CA" -> 141
    case-folded prefix on either segment   "Portland"     -> 11    "San Francisco"     -> 808

An exact overlap is a `WHERE false` wearing a filter's clothes. So the hard filter keeps
a job when it is remote and the user takes remote, when either side names no place, or
when a segment matches case-folded — and the *exact* city goes to the model as something
to reason about rather than a clause to die on. Recorded in DECISIONS.md.

**Salary is not filtered and does not appear in the funnel.** §3.5 lists it, but `jobs`
carries no salary column — the only structured pay data in the repo is one feed's
`raw_json`, on 100 of 13,725 rows. A filter that structurally cannot fire, reported in a
funnel as though it ran, is the purest form of green-while-broken. Trigger: a
`jobs.salary_min` column.
"""

import re
import uuid
from dataclasses import asdict, dataclass

from db.models import Job, Match
from schemas.enums import RemoteMode, WorkAuth
from schemas.prefs import Prefs
from schemas.profile import ProfileRead
from sqlalchemy import ARRAY, ColumnElement, Select, Text, and_, cast, func, literal, or_, select
from sqlalchemy.orm import Session

from workers import seniority as bands

# Phrases a posting uses to say it will not sponsor. Only ever consulted for a profile
# that needs sponsorship, and only ever to *drop* — a silent posting passes, because the
# alternative is hiding jobs from someone on a guess about what the employer left out.
_NO_SPONSORSHIP: tuple[str, ...] = (
    "not able to sponsor",
    "unable to sponsor",
    "cannot sponsor",
    "do not sponsor",
    "does not sponsor",
    "no visa sponsorship",
    "without sponsorship",
    "not provide sponsorship",
    "not offer sponsorship",
    "no sponsorship",
    "must be authorized to work",
    "must be authorised to work",
)

# How many bands apart a posting may be and still be worth scoring. A window, not a
# floor: a staff engineer should see neither an internship nor a director role, and
# `seniority.distance` is signed so one band up survives as a REACH.
_BAND_WINDOW = 1


@dataclass(frozen=True)
class Funnel:
    """Where the pool went. §3.7's "alert on volume, not just errors", for filters.

    Recorded on every run rather than only in tests, because the failure this guards
    against — a filter that drops everything — raises no exception and looks like a
    quiet day. There is no `dropped_salary`: see the module docstring.
    """

    pool: int
    already_scored: int
    dropped_location: int
    dropped_remote: int
    dropped_seniority: int
    dropped_work_auth: int
    dropped_keywords: int
    candidates: int

    def as_payload(self) -> dict[str, int]:
        return asdict(self)


def active(profile: ProfileRead, prefs: Prefs) -> list[str]:
    """Which filters actually constrained anything for this profile.

    Goes into `reasons_json`, and the distinction it carries is the useful one: every job
    that reaches the model passed every filter by construction, so a list of "filters
    passed" would be the same constant on every row. What a reader cannot otherwise tell
    is whether location was *checked and matched* or simply never set.
    """
    return [
        name
        for name, on in (
            ("location", bool(profile.locations)),
            ("remote", bool(prefs.remote_modes)),
            ("seniority", profile.seniority is not None),
            ("work_auth", profile.work_auth == WorkAuth.NEEDS_SPONSORSHIP),
            ("keywords", bool(prefs.must_have_keywords or prefs.exclude_keywords)),
        )
        if on
    ]


def candidates(
    session: Session, profile: ProfileRead, prefs: Prefs
) -> tuple[list[uuid.UUID], Funnel]:
    """The jobs worth spending money on, and the count of what each filter removed.

    Returns ids rather than rows so that nothing downstream can re-query the pool and
    quietly widen the set — see the package docstring.
    """
    rows = session.execute(_query(profile, prefs)).all()

    kept: list[uuid.UUID] = []
    counts = dict.fromkeys(
        ("location", "remote", "seniority", "work_auth", "keywords"),
        0,
    )
    already = 0
    for row in rows:
        if row.already_scored:
            already += 1
            continue
        # First failing filter wins the attribution, in ladder order. A job failing two
        # filters must be counted once or the funnel stops summing to the pool.
        for name, passed in (
            ("location", row.pass_location),
            ("remote", row.pass_remote),
            ("seniority", row.pass_seniority),
            ("work_auth", row.pass_work_auth),
            ("keywords", row.pass_keywords),
        ):
            if not passed:
                counts[name] += 1
                break
        else:
            kept.append(row.id)

    return kept, Funnel(
        pool=len(rows),
        already_scored=already,
        dropped_location=counts["location"],
        dropped_remote=counts["remote"],
        dropped_seniority=counts["seniority"],
        dropped_work_auth=counts["work_auth"],
        dropped_keywords=counts["keywords"],
        candidates=len(kept),
    )


def _query(
    profile: ProfileRead, prefs: Prefs
) -> Select[tuple[uuid.UUID, bool, bool, bool, bool, bool, bool]]:
    """One SELECT over the deduped pool, one boolean per filter.

    Booleans rather than a compound `WHERE` so the funnel can attribute a drop. The
    alternative — one query per filter — would scan the pool six times to learn the same
    thing.
    """
    haystack = func.concat_ws(" ", Job.title, func.coalesce(Job.description, ""))
    return select(
        Job.id,
        _already_scored(profile.user_id).label("already_scored"),
        _location(profile, prefs).label("pass_location"),
        _remote(prefs).label("pass_remote"),
        _seniority(profile).label("pass_seniority"),
        _work_auth(profile).label("pass_work_auth"),
        _keywords(prefs, haystack).label("pass_keywords"),
    ).where(
        # The deduped pool, and the only place this predicate is written.
        Job.closed_at.is_(None),
        Job.canonical_id.is_(None),
    )


def _already_scored(user_id: uuid.UUID) -> ColumnElement[bool]:
    """Jobs this user already has a score for. Not a filter — a cost control.

    Runs before everything and is what makes a run over an unchanged pool free. A match
    whose score is still NULL (its explain call failed) is deliberately *not* excluded,
    so the next run retries it.
    """
    return (
        select(literal(True))
        .where(Match.user_id == user_id, Match.job_id == Job.id, Match.score.is_not(None))
        .exists()
    )


def _location(profile: ProfileRead, prefs: Prefs) -> ColumnElement[bool]:
    """Coarse: remote counts, silence counts, and a segment match counts.

    The places come from `profiles.locations` (the promoted column), not from prefs —
    DECISIONS.md's split rule puts anything M4 filters on into a column.

    `unnest` on the job side with a case-folded comparison of each comma segment. See the
    module docstring for why this is not `profiles.locations && jobs.locations`.
    """
    if not profile.locations:
        return literal(True)

    wanted = [
        segment
        for place in profile.locations
        for part in place.split(",")
        if (segment := part.strip().lower())
    ]
    if not wanted:
        return literal(True)

    # One array expression rather than an EXISTS over `unnest`. Two forms of that were
    # tried first and both are traps: `table_valued(...).alias(...)` renders `AS job_loc`
    # with no column list so `job_loc.segment` does not exist, and wrapping the unnest in
    # a plain `.subquery()` renders it **uncorrelated** — it unnests every job's locations
    # at once, so EXISTS is true for every row as soon as one job in the table matches.
    # That version passed every "this should survive" assertion in the suite and failed
    # only the one asserting a specific row must not.
    #
    # Joining the array and re-splitting on `\s*,\s*` yields the trimmed segments of every
    # element in one array, which `&&` can then overlap against the profile's.
    segments = func.regexp_split_to_array(
        func.btrim(func.lower(func.array_to_string(Job.locations, ","))), r"\s*,\s*"
    )
    overlap = segments.op("&&")(cast(wanted, ARRAY(Text)))
    return or_(
        # The source named no place at all — unknown never drops.
        func.cardinality(Job.locations) == 0,
        # Remote, and the user takes remote. A remote job in a city they never named is
        # still a job they can do.
        and_(Job.remote_mode == RemoteMode.REMOTE.value, _takes_remote(prefs)),
        overlap,
    )


def _word(term: str) -> str:
    """A word-boundary POSIX pattern for one user-supplied term.

    `re.escape` because these are user input: a keyword of `C++` is a valid thing to want
    and an invalid regex, and an unescaped one raises from inside a SELECT rather than at
    the boundary where it could be explained.

    **The boundaries are conditional, which is the non-obvious half.** `\\m` and `\\M`
    anchor to a word *character*, so `\\mC\\+\\+\\M` can never match anything: the `\\M`
    demands a word character before it and `+` is not one. Applied unconditionally, every
    term ending in punctuation — `C++`, `.NET`, `Node.js` — becomes a filter that matches
    nothing, which for a must-have keyword means dropping the entire pool.
    """
    escaped = re.escape(term.strip())
    prefix = r"\m" if term.strip()[:1].isalnum() else ""
    suffix = r"\M" if term.strip()[-1:].isalnum() else ""
    return f"{prefix}{escaped}{suffix}"


def _takes_remote(prefs: Prefs) -> ColumnElement[bool]:
    """Empty means no preference, which includes remote. Never "no remote"."""
    return literal(not prefs.remote_modes or RemoteMode.REMOTE in prefs.remote_modes)


def _remote(prefs: Prefs) -> ColumnElement[bool]:
    """**Both halves of this are a measured trap.**

    `prefs.remote_modes == []` means no preference (schemas/prefs.py says so). Writing
    `remote_mode = ANY(:modes)` with an empty array returns nothing for every row —
    `x = ANY('{}')` is false for all x — so "no preference" would become "no jobs".

    And `remote_mode IS NULL` is 87% of the pool: the source did not say. Dropping those
    for a remote-preferring user removes every lever and greenhouse posting, which is the
    entire ATS layer M1 exists to produce.
    """
    if not prefs.remote_modes:
        return literal(True)
    return or_(
        Job.remote_mode.is_(None),
        Job.remote_mode.in_([mode.value for mode in prefs.remote_modes]),
    )


def _seniority(profile: ProfileRead) -> ColumnElement[bool]:
    """A window around the profile's band, computed in Python over the title keywords.

    Banding happens here rather than in SQL because `seniority.band` is one regex list
    with tests, and a second copy of it as a `CASE` expression is exactly the drift §3.1
    moved the module upward to prevent. The cost is that the whole pool's titles come
    back — which they do anyway, for the keyword filter.
    """
    if profile.seniority is None:
        return literal(True)

    # Bands within the window, as literal keyword matches on the title.
    allowed = [
        level
        for level in bands.RANK
        if abs(bands.RANK[level] - bands.RANK[profile.seniority]) <= _BAND_WINDOW
    ]
    forbidden = [
        word for level, words in bands.TITLE_BANDS if level not in allowed for word in words
    ]
    if not forbidden:
        return literal(True)

    # A title carrying an out-of-window keyword drops; anything else — including the
    # two-thirds of postings that state no band at all — passes.
    return ~or_(*[Job.title.op("~*")(_word(word)) for word in forbidden])


def _work_auth(profile: ProfileRead) -> ColumnElement[bool]:
    """Only consulted for someone who needs sponsorship, and only ever to drop.

    §7.2 calls this the single most-praised feature in the leading product, and its whole
    value is not showing someone jobs they cannot legally take. A posting that says
    nothing passes: the reverse error merely narrows their results, this one wastes an
    application.
    """
    if profile.work_auth != WorkAuth.NEEDS_SPONSORSHIP:
        return literal(True)
    return ~or_(
        *[func.coalesce(Job.description, "").ilike(f"%{phrase}%") for phrase in _NO_SPONSORSHIP]
    )


def _keywords(prefs: Prefs, haystack: ColumnElement[str]) -> ColumnElement[bool]:
    """Word-boundary, case-folded, over title + description.

    **Substring matching here is catastrophic, and it is the obvious way to write it.**
    Measured on the open pool: `description ILIKE '%go%'` matches 1,234 of 1,458 rows
    ("good", "going", "Google", "category", "Diego"); the whole-word form matches 136.
    The mirror image is worse — `exclude_keywords = ["go"]` as a substring would drop
    85% of the pool and look like a working filter.
    """
    clauses = [haystack.op("~*")(_word(word)) for word in prefs.must_have_keywords if word.strip()]
    clauses += [~haystack.op("~*")(_word(word)) for word in prefs.exclude_keywords if word.strip()]
    return and_(*clauses) if clauses else literal(True)
