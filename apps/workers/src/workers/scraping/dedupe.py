"""Cross-source dedupe (§4.2). M2's gate: one row survives, and it keeps the ATS URL.

Three layers now write into `jobs`, and the same role appears on a Greenhouse board, on
LinkedIn and on Remotive. §4.2 says those collapse to one, and that the survivor keeps
the **direct ATS** apply URL — because a Greenhouse form is something M9's extension and
M10's agent can drive, and an aggregator redirect is not.

Two mechanisms, both deliberately dumb:

  * a `dedupe_key` computed by pure functions below, so the rules are unit-testable
    against strings rather than through a container;
  * one SQL statement that points every loser at its group's winner, where the winner is
    chosen by a priority that is a pure function of `jobs.source` — never of arrival
    order, `created_at`, or which task happened to run first.

That second point is what makes "the survivor keeps the ATS apply URL" structural rather
than bookkeeping. If an ATS row is open in the group it has priority 0 and wins, and its
`url` already *is* the ATS URL. Nothing is copied, so nothing can be copied wrong.

Losers are marked, never deleted — see the 0005 migration for why deleting one starts an
endless insert/delete churn.

**The normalizers are deliberately conservative.** A false merge silently removes a real
job from the pool, which is strictly worse than a surviving duplicate: a duplicate costs
one wasted match, a false merge costs an application the user never got to make. So
seniority is never stripped, multi-city openings stay distinct, and there is no fuzzy
matching — a similarity threshold would also make the winner non-deterministic between
runs, which breaks the idempotency this stage depends on.
"""

import re
import unicodedata
from dataclasses import dataclass
from typing import Any

from db.models import Job
from schemas.enums import AtsType
from sqlalchemy import Text, case, cast, func, select, update
from sqlalchemy.orm import Session

# Layer 1 (direct ATS) beats everything, because §4.2's reason is apply-URL quality.
# Free feeds beat aggregators for the same reason: Remotive and RemoteOK routinely carry
# the employer's own link, while LinkedIn and Indeed carry a redirect. This ranks by URL
# quality, not by §4.1's coverage ordering — §4.2 fixes only ATS > aggregator and is
# silent on where feeds sit.
SOURCE_PRIORITY: dict[str, int] = {
    **{ats.value: 0 for ats in AtsType if ats is not AtsType.OTHER},
    "remotive": 1,
    "remoteok": 1,
    "himalayas": 1,
    "arbeitnow": 1,
    "weworkremotely": 1,
    "jobicy": 1,
    "workingnomads": 1,
    "themuse": 1,
}

# An unlisted source sorts last. A typo in an ATS key would therefore silently let an
# aggregator outrank a first-party row and cost the survivor its apply URL, which is why
# test_dedupe.py cross-checks this dict against ADAPTERS and FEEDS.
UNKNOWN_PRIORITY = 99

# Layer 2's sources are `jobspy:{site}` and cannot be listed one by one without pinning
# the site list in two places. Anything under that prefix ranks below every feed.
AGGREGATOR_PREFIX = "jobspy:"
AGGREGATOR_PRIORITY = 2

# Dropped from the end of a company name, and never when it is the only token —
# "Limited" alone is a company, "Acme Limited" is Acme.
_LEGAL_SUFFIXES = frozenset(
    {
        "inc", "incorporated", "llc", "llp", "ltd", "limited", "corp", "corporation",
        "co", "company", "gmbh", "mbh", "ag", "se", "bv", "nv", "sa", "sas", "srl",
        "spa", "ab", "as", "oy", "plc", "pty", "pte", "kk", "kft", "aps", "sro", "ug",
    }
)  # fmt: skip

# Expanded, not stripped. This only ever merges titles that already mean the same thing;
# removing a seniority word would merge titles that do not.
_ABBREVIATIONS = {"sr": "senior", "snr": "senior", "jr": "junior", "mgr": "manager"}

# "(m/w/d)", "(f/m/x)", "(all genders)" — an EU convention, and pure noise for matching.
_GENDER_TAG = re.compile(r"\(\s*(?:[mfwdx](?:\s*/\s*[mfwdx])+|all\s+genders?)\s*\)\s*$", re.I)

# A trailing requisition id, and only behind a delimiter. Requiring one is what keeps
# "Summer Internship 2026" and "…2025" apart while still merging "Sr. Manager - 744123".
#
# `#` is deliberately NOT a delimiter here. Live Gopuff data reads "Operations
# Associate, Bridgeport, #259" — that is a *store number*, part of which job this is,
# and stripping it merged two postings at two different facilities. A dash or a bracket
# is a requisition id; a hash is not reliably anything.
_REQ_ID = re.compile(r"[-–—(\[]\s*(?:req[-\s]?)?[a-z]{0,3}[-\s]?\d{3,}\s*[)\]]?\s*$", re.I)

_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Real keys always contain this; a uuid rendered as text never does. That is what lets
# un-keyable rows share a partition namespace with keyed ones without colliding.
_SEPARATOR = "|"


def _squash(value: str) -> str:
    """Lowercase, strip accents, drop everything that is not a letter or a digit."""
    folded = unicodedata.normalize("NFKD", value.lower())
    stripped = "".join(char for char in folded if not unicodedata.combining(char))
    return _NON_ALNUM.sub("", stripped)


def normalize_company(name: str) -> str:
    """ "Acme Inc" and "Acme" are one employer. "Société Générale S.A." is one word."""
    folded = unicodedata.normalize("NFKD", name.lower())
    stripped = "".join(char for char in folded if not unicodedata.combining(char))
    # Periods go before tokenizing, so "S.A." and "L.L.C." arrive as one token the
    # suffix list can recognise instead of as a run of single letters it cannot.
    tokens = [token for token in _NON_ALNUM.split(stripped.replace(".", "")) if token]
    if len(tokens) > 1 and tokens[-1] in _LEGAL_SUFFIXES:
        tokens = tokens[:-1]
    return "".join(tokens)


def normalize_title(title: str) -> str:
    """The conservative one. Removes noise; never removes meaning.

    Order matters: the gender tag and the requisition id both sit at the end, and
    stripping the tag first exposes an id that was hiding behind it.
    """
    cleaned = _GENDER_TAG.sub("", title.strip())
    cleaned = _REQ_ID.sub("", cleaned).strip()
    cleaned = cleaned.replace("&", " and ")

    folded = unicodedata.normalize("NFKD", cleaned.lower())
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    tokens = [token for token in _NON_ALNUM.split(folded) if token]
    return "".join(_ABBREVIATIONS.get(token, token) for token in tokens)


def normalize_location(location: str | None) -> str:
    """The first comma-separated component only.

    That one rule collapses "San Francisco, CA" / "San Francisco, California, United
    States" / "San Francisco" without a fifty-entry state table, and it keeps multi-city
    openings of one title apart because their first components differ.

    ponytail: "Portland, OR" and "Portland, ME" merge if one employer posts the same
    exact title in both. Add a city+region map when a real board produces that; a state
    abbreviation table is not worth carrying before then.
    """
    if not location:
        return ""
    return _squash(location.split(",")[0])


def dedupe_key(company: str, title: str, location: str | None) -> str | None:
    """`company|title|location`, or None when the row cannot be keyed safely.

    Every component is squashed to `[a-z0-9]`, so none of them can contain the
    separator and two different triples can never render to one key.
    """
    normalized_company = normalize_company(company or "")
    normalized_title = normalize_title(title or "")
    if not normalized_company or not normalized_title:
        return None
    return _SEPARATOR.join((normalized_company, normalized_title, normalize_location(location)))


@dataclass(frozen=True)
class DedupeResult:
    keyed: int  # open rows given a dedupe_key this run — 0 in steady state
    duplicates: int  # open rows that became, or changed, a duplicate
    promoted: int  # open rows that stopped being a duplicate

    @property
    def changed(self) -> int:
        return self.keyed + self.duplicates + self.promoted


def _priority() -> Any:
    """Rank an open row by its source, entirely independently of when it arrived."""
    return case(
        (Job.source.startswith(AGGREGATOR_PREFIX), AGGREGATOR_PRIORITY),
        *[(Job.source == source, rank) for source, rank in SOURCE_PRIORITY.items()],
        else_=UNKNOWN_PRIORITY,
    )


def dedupe_jobs(session: Session) -> DedupeResult:
    """Key what needs keying, then converge every open row on its group's winner.

    A plain function over `(session)` for the same reason `ingest_company` is one:
    Part 10's rule is seed, run, assert the delta, and a task would put a broker between
    the test and the assertion. Commits nothing.

    Runs over the whole open set rather than per company: an aggregator or feed row may
    carry no `company_id` at all — §4.3's reverse-index is asynchronous and best-effort —
    so the key is the normalized company *text*, not the foreign key.
    """
    keyed = _fill_missing_keys(session)

    ranked = (
        select(
            Job.id,
            Job.source,
            func.first_value(Job.source)
            .over(
                partition_by=func.coalesce(Job.dedupe_key, cast(Job.id, Text)),
                order_by=(_priority(), Job.id),
            )
            .label("winner_source"),
            func.first_value(Job.id)
            .over(
                # coalesce, not a WHERE: an un-keyable row has to land in its own
                # singleton partition so it stays a survivor, rather than being skipped
                # with a stale canonical_id — and rather than every NULL-keyed row
                # collapsing into one enormous partition with a single arbitrary winner.
                partition_by=func.coalesce(Job.dedupe_key, cast(Job.id, Text)),
                # A total order: priority is a pure function of source, and id is a
                # unique primary key. uuidv7 sorts oldest-first, so an equal-priority
                # newcomer can never take the crown from an incumbent.
                order_by=(_priority(), Job.id),
            )
            .label("winner_id"),
        )
        .where(Job.closed_at.is_(None))
        .cte("ranked")
    )
    # Only ever collapse ACROSS sources. Two postings from one board that share a title
    # and a city are the employer opening two headcount, and that board's own ids
    # already say so — §4.2 is about an aggregator row matching an ATS row, not about
    # second-guessing a source's own listing. Verified against live Gopuff data, where
    # every single same-source "duplicate" was a distinct opening at a distinct site.
    canonical = case(
        (ranked.c.winner_source == Job.source, None),
        else_=func.nullif(ranked.c.winner_id, Job.id),
    )

    assigned = session.execute(
        update(Job)
        .where(
            Job.id == ranked.c.id,
            # The whole of "writes only diffs" for this stage. Without it every run
            # rewrites every open row's updated_at.
            Job.canonical_id.is_distinct_from(canonical),
        )
        .values(canonical_id=canonical, updated_at=func.now())
        .returning(Job.id, Job.canonical_id)
    ).all()
    session.flush()

    duplicates = sum(1 for row in assigned if row.canonical_id is not None)
    return DedupeResult(keyed=keyed, duplicates=duplicates, promoted=len(assigned) - duplicates)


def _fill_missing_keys(session: Session) -> int:
    """Key every open row that has none.

    The M1 backfill and the steady-state path are the same statement: after the first
    run this selects zero rows, forever.

    Not done inside `upsert`: its diff predicate blocks the write for every row whose
    payload has not changed, so a key added to `_MUTABLE` would never reach a single
    pre-existing row. `upsert` instead clears the key when a row genuinely changes, and
    the next pass recomputes it — one owner of the normalizer.

    ponytail: loads every keyless open row at once. Chunk it if a real board makes that
    hurt; at M2's volumes it is one short pass after the first.
    """
    rows = session.execute(
        select(Job.id, Job.company, Job.title, Job.location).where(
            Job.closed_at.is_(None), Job.dedupe_key.is_(None)
        )
    ).all()
    if not rows:
        return 0

    session.execute(
        update(Job),
        [
            {"id": row.id, "dedupe_key": dedupe_key(row.company, row.title, row.location)}
            for row in rows
        ],
    )
    session.flush()
    return len(rows)
