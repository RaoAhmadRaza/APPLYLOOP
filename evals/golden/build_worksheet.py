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
STRATA = {"on_topic": 4, "filtered_out": 4, "candidate_random": 3, "pool_random": 3}

# Crafts the fixture résumés are in, used only to make `on_topic` plausibly on-craft.
# Deliberately crude: this stratum decides where to *look* for positives, never what the
# label is. A human still decides every label.
ON_TOPIC_WORDS = ("engineer", "developer", "backend", "platform", "infrastructure", "data")


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
                "parsed_json": profile.model_dump(mode="json"),
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

    # Titles only, and only for the candidates — the full payloads are fetched at the end
    # for the dozen rows actually picked. The pool is five figures; materialising all of
    # it to read four titles would make this script the slowest thing in the repo.
    titles = dict(session.execute(select(Job.id, Job.title).where(Job.id.in_(candidates))).all())
    on_topic = [
        job_id
        for job_id in candidates
        if any(word in titles[job_id].lower() for word in ON_TOPIC_WORDS)
    ]

    chosen: list[tuple[str, uuid.UUID]] = []
    taken: set[uuid.UUID] = set()
    # `filtered_out` arrives already ordered and must NOT be shuffled again — the
    # round-robin across filters *is* its order, and re-shuffling silently threw it away.
    # The first draw after adding the round-robin still produced zero work-auth pairs for
    # exactly that reason, while looking entirely plausible.
    for stratum, source, preordered in (
        ("on_topic", on_topic, False),
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
