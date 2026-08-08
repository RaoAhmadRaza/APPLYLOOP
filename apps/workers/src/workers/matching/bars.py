"""Disqualifiers a `WHERE` clause could find, computed here instead of asked of a model.

**Why this is separate from `MatchFacts.disqualifiers`, and why only this one gates.**
The model reads a posting and quotes a refusal — real comprehension. These checks need no
comprehension at all, and asking for them measurably made the model worse:
extraction was 8/8 when the prompt was responsible only for clauses that must be read, and
9/13 after two more categories were added to the same rule. Attention is finite and a
prompt is not a list.

Measured on the 121-pair gate run, that difference is not stylistic: these checks produced
10 correct rejections and **0** spurious, the model 6 correct and ~11 spurious. So a bar
zeroes a score and a model-quoted disqualifier is advisory — see `score.score`.

They also fail differently. A model miss is invisible and moves between runs; a bug here
fails a unit test the same way every time. §3.5's ordering applied one rung lower: never
ask a model for something a pure function can decide.

**Not hard filters.** These are irreversible in a `WHERE` clause and merely wrong in a
score. A filter that drops a real match removes it before anything can measure the
mistake; a score of 0 is visible in the golden set as a lost positive. BAR.md floors
filter recall at 0.90 for that reason, and none of these is confident enough to spend
that budget.

Every check keeps `filters.py`'s polarity: **silence passes.** An unmappable location, an
unstated language, an absent eligibility clause, a job family the résumé has worked in and
a timezone window the profile's own region spans all yield nothing.
"""

import re

from db.models import Job
from schemas.profile import ProfileRead

from workers import regions

# Languages a posting can require, and what the profile must show to satisfy it. Only
# languages the pool actually demands — roughly a quarter of it is German-language, and
# the fixtures met Mandarin and French.
_LANGUAGE_DEMANDS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    (
        "Mandarin",
        r"bilingual [^.]{0,30}mandarin|mandarin (?:is )?required",
        ("mandarin", "chinese"),
    ),
    ("German", r"fluent in german|flie\wend|deutschkenntnisse in wort", ("german", "deutsch")),
    ("French", r"fluent in french|couramment (?:le )?fran\wais", ("french", "français")),
)

# A programme open only to people at a career stage. The profile fails it by being past
# that stage, which no amount of skill overlap offsets — and these postings read as an
# excellent match, because the craft really is the same.
_ELIGIBILITY = re.compile(
    r"current university students and recent graduates"
    r"|open (?:only )?to (?:current )?students"
    r"|must be currently enrolled",
    re.I,
)

# Bands that are not an early-career intake. A profile at or above these fails an
# eligibility window; a junior or a student does not.
_SENIOR_BANDS = frozenset({"mid", "senior", "staff", "principal", "lead"})

# Job families that are not engineering, however much engineering vocabulary the posting
# carries. A sales engineer's posting really does ask for Python and AWS, which is exactly
# why coverage scores it well and why this cannot be left to the ratio.
#
# **This is a family check, not a craft taxonomy, and the distinction is measured.** In the
# golden set `CLI Engineer`, `Data Engineer` and `Senior DevOps Engineer` are each
# `relevant` for one profile and `not_relevant` for another — identical titles, opposite
# labels — because the labeller was judging skill depth against the posting body, not the
# title's family. Any rule that tried to read backend-vs-data-vs-infra off a title would
# have to get those three wrong in one direction or the other. BAR.md §6 R5 agrees: it puts
# backend, platform, infrastructure and SRE on **one** chain.
#
# So this bars only what no engineering profile in the set is a candidate for, and the test
# that matters is the negative one — it fires on none of the 40 pairs labelled relevant.
_FAMILIES: tuple[tuple[str, tuple[str, ...], tuple[str, ...]], ...] = (
    (
        "sales engineering",
        ("sales engineer", "solutions engineer", "solution engineer", "account executive"),
        ("sales engineer", "solutions engineer", "account executive", "quota"),
    ),
    (
        "customer-facing architecture",
        ("solutions architect", "solution architect"),
        ("solutions architect", "solution architect", "presales", "pre-sales"),
    ),
    (
        "technical support",
        ("support engineer", "technical support"),
        ("support engineer", "technical support", "helpdesk", "service desk"),
    ),
)


# A stated working-hours window: a named zone and how far either side of it the employer
# will go. Both halves are required — a posting that merely mentions CET has stated no
# window, and inventing a tolerance would be this file guessing.
_TIMEZONE_WINDOW = re.compile(
    r"\b(CET|CEST|EET|EEST|WET|GMT|UTC|BST|EST|EDT|CST|PST|PDT|IST)\b"
    r"[^.\n]{0,20}?[+±]/?-\s*(\d+(?:\.\d+)?)\s*h",
    re.I,
)

# What those zone names are worth in UTC hours. Daylight variants included as themselves
# rather than normalised, because a posting saying CEST means the summer window.
_ZONE_OFFSETS: dict[str, float] = {
    "WET": 0.0,
    "GMT": 0.0,
    "UTC": 0.0,
    "BST": 1.0,
    "CET": 1.0,
    "CEST": 2.0,
    "EET": 2.0,
    "EEST": 3.0,
    "IST": 5.5,
    "EST": -5.0,
    "EDT": -4.0,
    "CST": -6.0,
    "PST": -8.0,
    "PDT": -7.0,
}


def check(job: Job, profile: ProfileRead, resume: str) -> list[str]:
    """Every deterministic bar this pair trips. Empty is the common case."""
    found = [
        _country_scope(job, profile),
        _language(job, resume),
        _eligibility(job, profile),
        _family(job, resume),
        _timezone(job, profile),
    ]
    return [bar for bar in found if bar]


def _country_scope(job: Job, profile: ProfileRead) -> str | None:
    """A role scoped to somewhere the profile may not legally work.

    The case no quoted span can catch: "Remote — United States" states a location, not a
    refusal, so there is nothing in the posting for a model to quote. It is still a job
    the candidate cannot take, and BAR.md §6 rule 3's stated purpose — not showing
    someone jobs they cannot legally take — does not stop at postings that spell it out.

    Silence passes three ways, because each is a different kind of not-knowing: the
    profile never stated its authorisation, the posting named no place we recognise, or
    the posting declined to name one at all.
    """
    if profile.work_auth_regions is None:
        return None
    places = list(job.locations or []) + ([job.location] if job.location else [])
    if not places:
        return None

    # **Named regions win over the global phrases.** "Remote, United States" contains the
    # word remote and is still scoped to one country; checking globality first would read
    # every US remote posting as open to the world, which is the single most common
    # location string in the pool.
    joined = " ".join(places)
    wanted = regions.named_in(joined)
    if not wanted or regions.is_open_to_the_world(joined, wanted):
        return None
    authorised = set(profile.work_auth_regions)
    if regions.covers(authorised, wanted):
        return None
    return (
        f"role is based in {'/'.join(sorted(wanted))}; "
        f"profile is authorised to work in {'/'.join(sorted(authorised)) or 'nowhere stated'}"
    )


def _language(job: Job, resume: str) -> str | None:
    """A working language the posting requires and the résumé never mentions.

    Only fires on an explicit demand — "Bilingual English/Mandarin is required", not a
    posting that happens to be written in German. The résumé is searched for the language
    by name, which is how a candidate who speaks it says so.
    """
    haystack = f"{job.title} {job.description or ''}"
    lowered = resume.lower()
    for name, demand, evidence in _LANGUAGE_DEMANDS:
        if re.search(demand, haystack, re.I) and not any(word in lowered for word in evidence):
            return f"posting requires {name}; the résumé does not mention it"
    return None


def _family(job: Job, resume: str) -> str | None:
    """A job family the résumé shows no evidence of ever having worked in.

    Pair-relative like every other bar here, and for a reason this file has been burnt by
    once: a rule keyed on the posting alone is a keyword blocklist, and a real sales
    engineer must still be shown sales-engineering roles. The résumé is the evidence, so
    the check disappears for exactly the candidate it would be wrong for.

    Read off the **title**, never the description — "works closely with our solutions
    architects" appears in plenty of backend postings and says nothing about the role.
    """
    title = (job.title or "").lower()
    lowered = resume.lower()
    for name, titles, evidence in _FAMILIES:
        if any(word in title for word in titles) and not any(word in lowered for word in evidence):
            return f"role is {name}; the résumé shows no experience in it"
    return None


def _timezone(job: Job, profile: ProfileRead) -> str | None:
    """A stated working-hours window the profile's own location cannot reach.

    **The category the model was carrying until it was demoted to advisory**, and the one
    the other bars here structurally could not compute: `Located in the CET timezone (+/- 3
    hours), we are unable to consider applications from candidates in other time zones`
    costs two false positives on the reporting split at 69 and 55.

    It is the sharpest pair-relative case in the file. Four postings in the golden set
    carry that exact sentence; two are labelled `relevant` and two are not, and the
    sentence is identical in all four. Only the candidate differs — Kraków sits inside the
    window and Portland is nine hours outside it. A rule reading the posting alone would
    look like a precision fix and would delete both true positives.

    Silence passes four ways, and the fourth is the one that keeps this honest: the
    posting stated no window, or stated a zone this table cannot price, or the profile
    named no location, or the region it named **spans** the window. A country is not a
    timezone — the US runs from -10 to -4 — so a range is compared rather than a point and
    a bar needs every named region to be wholly outside. Anything a range leaves open
    stays open.
    """
    if not profile.locations:
        return None
    haystack = " ".join(list(job.locations or []) + [job.location or "", job.description or ""])
    match = _TIMEZONE_WINDOW.search(haystack)
    if match is None:
        return None
    centre = _ZONE_OFFSETS.get(match.group(1).upper())
    if centre is None:
        return None
    tolerance = float(match.group(2))
    low, high = centre - tolerance, centre + tolerance

    spans = [
        regions.offsets(code) for place in profile.locations for code in regions.named_in(place)
    ]
    known = [span for span in spans if span is not None]
    if not known or any(span[0] <= high and span[1] >= low for span in known):
        return None
    return (
        f"posting requires {match.group(1).upper()} +/- {match.group(2)} hours "
        f"(UTC{low:+g} to UTC{high:+g}); the profile is in {', '.join(profile.locations)}"
    )


def _eligibility(job: Job, profile: ProfileRead) -> str | None:
    """An intake open only to students or recent graduates, against someone past that."""
    if (profile.seniority or "").lower() not in _SENIOR_BANDS:
        return None
    match = _ELIGIBILITY.search(job.description or "")
    if match is None:
        return None
    return f"programme is limited to {match.group(0).lower()}; profile is {profile.seniority}"
