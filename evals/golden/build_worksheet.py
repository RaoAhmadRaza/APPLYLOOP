"""Build the blank labelling worksheet for M4's golden set.

Run once, against a database holding a real job pool:

    uv run python evals/golden/build_worksheet.py

Writes `evals/golden/pairs.json` with every `label` field null, for a human to fill in.
Re-running with the same seed reproduces the same draw; the seed is committed in the
output, so nobody can quietly re-roll until the sample looks convenient.

**Why this is a script and not a fixture.** The sample must come from the real pool
(§7 of BAR.md), and it must be drawn *once* and then frozen — a set regenerated on every
test run is not a golden set, it is a moving target that can never be labelled.

**Why it embeds nothing.** Building the strata needs SQL only. The plan originally had a
"passes filters, low cosine" stratum, which would have required embedding the entire
candidate pool before a single pair was labelled — the exact spend §3.5's ladder exists to
avoid, and on a matcher whose quality is still unmeasured. A uniform draw from the
candidates catches the same retrieval false negatives, costs nothing, and has the property
that actually matters here: it is independent of the matcher under test.

The profiles come from running M3's real parse over the committed résumé fixtures. Not
hand-written: a hand-written `parsed_json` would mean the filters under test are being fed
by the labeller rather than by the stage that will feed them in production.
"""

import hashlib
import json
import os
import pathlib
import re
import sys
import uuid
from typing import Any

from db.models import Job
from db.session import make_sync_engine, make_sync_sessionmaker
from schemas.prefs import Prefs
from schemas.profile import ProfileRead
from schemas.resume import ParsedResume
from sqlalchemy import func, select
from sqlalchemy.orm import Session
from workers import llm
from workers.matching import filters
from workers.profiles import derive, extract
from workers.profiles import prompt as parse_prompt

HERE = pathlib.Path(__file__).parent
FIXTURES = HERE.parents[1] / "tests" / "fixtures" / "resumes"
OUTPUT = HERE / "pairs.json"

# Committed in the output so the draw is reproducible and cannot be re-rolled.
SEED = "applyloop-m4-golden-2026-08"

# BAR.md §7. Per profile, so four profiles produce roughly 50 pairs after the per-stratum
# caps are hit — the sizes are targets, not guarantees, because a stratum can be short on
# a small pool and inventing rows to fill it would be worse than reporting the shortfall.
# BAR.md needs at least 10 `relevant` labels for precision to mean anything, and the
# first draw produced 7 — so `on_topic` is much the largest stratum. Everything else is
# there to keep the set honest rather than to supply positives.
STRATA = {"on_topic": 8, "filtered_out": 4, "candidate_random": 2, "pool_random": 2}

# How many of the profile's own skills a posting must name to count as on-craft. One
# would let a single "Python" in a marketing job's tooling list through; two is what
# separates a role in the craft from a role that merely mentions it.
_MIN_SKILL_HITS = 2

# At most this many on-craft pairs from one employer. The pool is thick with
# aggregator reposts, and without a cap a single employer fills the stratum.
_MAX_PER_COMPANY = 2


def main() -> int:
    if OUTPUT.exists():
        print(f"{OUTPUT} already exists — refusing to overwrite a labelled set.")
        return 1
    if not llm.is_configured():
        print("LLM_API_KEY is not set. The profiles must come from M3's real parse.")
        return 1

    engine = make_sync_engine(os.environ["DATABASE_URL"])
    sessions = make_sync_sessionmaker(engine)

    entries: list[dict[str, Any]] = []
    profiles: dict[str, Any] = {}
    with sessions() as session:
        pool = session.scalar(
            select(func.count(Job.id)).where(Job.closed_at.is_(None), Job.canonical_id.is_(None))
        )
        for path in sorted(FIXTURES.iterdir()):
            if path.suffix not in {".pdf", ".docx", ".txt"}:
                continue
            print(f"parsing {path.name} ...", flush=True)
            profile, resume_text = _parse(path)
            profiles[path.name] = {
                # The résumé, not the whole ProfileRead — the live gate seeds this
                # straight into `profiles.parsed_json`, and a wrapped copy would put a
                # ProfileRead dump in the column M4 reads a résumé out of.
                "parsed_json": profile.parsed_json,
                "master_resume": resume_text,
                "locations": profile.locations,
                "seniority": profile.seniority,
                "work_auth": profile.work_auth,
            }
            entries.extend(_sample(session, path.name, profile))

    OUTPUT.write_text(
        json.dumps(
            {
                "_meta": {
                    "seed": SEED,
                    "bar": "evals/golden/BAR.md",
                    "pool_size_at_draw": pool,
                    "strata": STRATA,
                    "labels": ["relevant", "not_relevant", "borderline"],
                    "instructions": (
                        "Fill `label` for every pair. Read BAR.md §6 first — the six "
                        "ambiguous cases are decided there so two labellers agree. Set "
                        "`labelled_by` to 'human:<your initials>'. Mark each "
                        "not_relevant pair's `negative_type` as 'hard' or 'easy'."
                    ),
                },
                "profiles": profiles,
                "pairs": entries,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(f"wrote {len(entries)} pairs to {OUTPUT}")
    return 0


def _parse(path: pathlib.Path) -> tuple[ProfileRead, str]:
    """One résumé through M3's real stage, minus the database write."""
    resume_text = extract.to_markdown(path.read_bytes(), path.suffix)
    parsed = llm.complete_json(
        ParsedResume, system=parse_prompt.SYSTEM, user=parse_prompt.build(resume_text)
    )
    years = derive.years_of_experience(parsed)
    return (
        ProfileRead(
            id=uuid.uuid4(),
            user_id=uuid.uuid4(),
            master_resume=resume_text,
            parsed_json=parsed.model_dump(mode="json"),
            prefs_json={},
            locations=derive.locations(parsed),
            seniority=derive.seniority(parsed, years),
            work_auth=derive.work_auth(parsed),
            salary_floor=None,
            created_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
            updated_at="2026-01-01T00:00:00Z",  # type: ignore[arg-type]
        ),
        resume_text,
    )


def _sample(session: Session, fixture: str, profile: ProfileRead) -> list[dict[str, Any]]:
    """Four strata for one profile, drawn from the open pool by a seeded shuffle."""
    rows = session.execute(filters._query(profile, Prefs())).all()
    verdicts = {
        row.id: [
            name
            for name, passed in (
                ("location", row.pass_location),
                ("remote", row.pass_remote),
                ("seniority", row.pass_seniority),
                ("work_auth", row.pass_work_auth),
                ("keywords", row.pass_keywords),
            )
            if not passed
        ]
        for row in rows
    }
    candidates = [job_id for job_id, failed in verdicts.items() if not failed]
    single_failure = {job_id: failed[0] for job_id, failed in verdicts.items() if len(failed) == 1}

    on_topic = _on_craft(session, candidates, profile)

    chosen: list[tuple[str, uuid.UUID]] = []
    taken: set[uuid.UUID] = set()
    # `filtered_out` arrives already ordered and must NOT be shuffled again — the
    # round-robin across filters *is* its order, and re-shuffling silently threw it away.
    # The first draw after adding the round-robin still produced zero work-auth pairs for
    # exactly that reason, while looking entirely plausible.
    for stratum, source, preordered in (
        # `on_topic` is ranked by skill overlap and `filtered_out` is round-robined
        # across filters. Both orderings ARE the stratum; shuffling either throws away
        # the only thing that made it worth drawing.
        ("on_topic", on_topic, True),
        ("filtered_out", _across_filters(single_failure, fixture), True),
        ("candidate_random", candidates, False),
        ("pool_random", list(verdicts), False),
    ):
        wanted = STRATA[stratum]
        for job_id in source if preordered else _shuffled(source, fixture + stratum):
            if wanted == 0:
                break
            if job_id in taken:
                continue
            taken.add(job_id)
            chosen.append((stratum, job_id))
            wanted -= 1

    jobs = _jobs(session, [job_id for _, job_id in chosen])
    return [
        {
            "profile": fixture,
            "job_id": str(job_id),
            "stratum": stratum,
            "expected_filter": single_failure.get(job_id),
            "job": jobs[job_id],
            # Blank, for a human. The loader refuses to count a pair without one.
            "label": None,
            "labelled_by": None,
            "negative_type": None,
        }
        for stratum, job_id in chosen
    ]


def _on_craft(
    session: Session, candidates: list[uuid.UUID], profile: ProfileRead
) -> list[uuid.UUID]:
    """Candidates whose posting names at least two of the profile's own skills.

    **This replaces matching generic title words, which did not work.** The first draw
    used ("engineer", "developer", "data", …) against the title, and produced an
    `on_topic` stratum of RF engineers, mechanical engineers, equipment-qualification
    engineers and mobile QA — the word "engineer", four unrelated crafts. Labelled
    honestly, the whole 56-pair set yielded **7 relevant against a bar of 10**, which
    makes precision unmeasurable.

    Skills come from the résumé the M3 parse produced, so this stays a property of the
    candidate rather than a hand-written list of what we hope to find. It decides only
    where to *look* for positives; a human still decides every label, and the other three
    strata are untouched so the draw as a whole is still matcher-independent.

    Ordered by how many skills matched, so the most plausible pairs are drawn first —
    that ordering is the point, and unlike the shuffled strata it must not be reshuffled.
    """
    terms = _skills(profile)
    if not terms or not candidates:
        return []

    # Counted in Python, over one plain SELECT. The first attempt put one regex per skill
    # into SQL — nineteen scans over full descriptions across seven thousand candidates
    # per profile — and had to be killed after minutes. One compiled alternation over a
    # truncated description is a single pass per row and is trivially readable.
    #
    # The description is cut because a posting's requirements sit near the top; the tail
    # is benefits and equal-opportunity boilerplate, which no skill should match against.
    pattern = re.compile(
        r"\b(" + "|".join(re.escape(term) for term in terms) + r")\b", re.IGNORECASE
    )
    rows = session.execute(
        select(
            Job.id,
            Job.title,
            Job.company,
            func.left(func.coalesce(Job.description, ""), 4000),
        ).where(Job.id.in_(candidates))
    ).all()

    scored: list[tuple[int, str, uuid.UUID, tuple[str, str]]] = []
    for job_id, title, company, description in rows:
        # Distinct skills, not total mentions: a posting that says "Python" twelve times
        # is not more on-craft than one naming Python, Kafka and Terraform once each.
        found = {match.group(1).lower() for match in pattern.finditer(f"{title} {description}")}
        if len(found) >= _MIN_SKILL_HITS:
            scored.append((len(found), str(job_id), job_id, (company, title.strip().lower())))

    # **One posting per role, and at most two per employer.** Ranking by overlap alone
    # put eight copies of one requisition into a stratum of eight: the open pool holds
    # 101 rows of `Bluelight Consulting / senior software engineer (flask/react)` and 42
    # of one Jobgether posting, all with distinct `external_id`s, so `dedupe_key` never
    # collapsed them. Eight identical pairs measure one judgement eight times.
    #
    # That duplication is a finding about the pool rather than about this script — see
    # DECISIONS.md, where fuzzy dedupe was deferred with exactly this trigger.
    seen_roles: set[tuple[str, str]] = set()
    per_company: dict[str, int] = {}
    ordered: list[uuid.UUID] = []
    for _hits, _tiebreak, job_id, role in sorted(scored, key=lambda row: (-row[0], row[1])):
        company = role[0]
        if role in seen_roles or per_company.get(company, 0) >= _MAX_PER_COMPANY:
            continue
        seen_roles.add(role)
        per_company[company] = per_company.get(company, 0) + 1
        ordered.append(job_id)
    return ordered


def _skills(profile: ProfileRead) -> list[str]:
    """The résumé's own skill words, deduplicated and long enough to mean something.

    **The unpacking is not defensive padding — one of the four fixtures needs it.** M3's
    parse of `two_column.pdf` returns three skills whose `name` is the entire résumé
    line, `"ML: PyTorch, scikit-learn, MLflow"`, with `keywords` empty. Matched literally
    those three strings appear in no posting on earth, which is why that profile drew
    **zero** on-craft candidates out of 5,488 while looking like a pool problem.

    So: prefer `keywords`; otherwise take the part after any `"Category:"` label and
    split on commas. Dropping the label matters — "Data" and "Languages" as search terms
    would match most of the pool and drown the real technologies.
    """
    parsed = ParsedResume.model_validate(profile.parsed_json)
    terms: list[str] = []
    for skill in parsed.skills:
        if skill.keywords:
            terms += skill.keywords
        elif skill.name:
            _, _, tail = skill.name.partition(":")
            terms += (tail or skill.name).split(",")
    # Two characters would match "R" and "C" against half the pool; the profiles that
    # matter here name real technologies.
    return sorted({term.strip() for term in terms if len(term.strip()) > 2})


def _across_filters(single_failure: dict[uuid.UUID, str], salt: str) -> list[uuid.UUID]:
    """Round-robin the `filtered_out` draw across the filters that actually fired.

    A plain shuffle takes whatever is most common, and on the real pool that is location
    and seniority — the first draw produced 15 location drops, 8 seniority, and **zero**
    work-auth, which is the one §7.2 calls the most-praised feature in the leading
    product. A stratum that cannot demonstrate the filter the gate most cares about is
    not doing its job.

    Rare filters go first so a small quota still reaches them.
    """
    by_filter: dict[str, list[uuid.UUID]] = {}
    for job_id, name in single_failure.items():
        by_filter.setdefault(name, []).append(job_id)

    queues = [
        _shuffled(ids, salt + name)
        for name, ids in sorted(by_filter.items(), key=lambda item: len(item[1]))
    ]
    ordered: list[uuid.UUID] = []
    for round_index in range(max((len(queue) for queue in queues), default=0)):
        for queue in queues:
            if round_index < len(queue):
                ordered.append(queue[round_index])
    return ordered


def _jobs(session: Session, job_ids: list[uuid.UUID]) -> dict[uuid.UUID, dict[str, Any]]:
    """The payload stored beside each pair, so the set does not rot when a job closes."""
    rows = session.scalars(select(Job).where(Job.id.in_(job_ids))).all()
    return {
        job.id: {
            "title": job.title,
            "company": job.company,
            "locations": list(job.locations),
            "remote_mode": job.remote_mode,
            "url": job.url,
            "source": job.source,
            "description": (job.description or "")[:12_000],
        }
        for job in rows
    }


def _shuffled(items: list[uuid.UUID], salt: str) -> list[uuid.UUID]:
    """A seeded, reproducible order. `random` is avoided so the draw is a pure function
    of (SEED, salt, job id) and does not depend on interpreter or call order."""
    return sorted(
        items, key=lambda item: hashlib.sha256(f"{SEED}{salt}{item}".encode()).hexdigest()
    )


if __name__ == "__main__":
    sys.exit(main())
