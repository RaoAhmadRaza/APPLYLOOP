"""M5's gate: the fabrication cases through a real strong model. `make verify-live-tailor`.

**Nothing is stubbed at the `llm` boundary.** The offline guard puts adversarial *output*
in front of the validator; this puts adversarial *input* in front of a real model and
checks what comes back. Both are needed and M3's lesson says why — a stubbed model tests
the plumbing while the judgement stays unmeasured — so the plumbing is tested for free and
the money is spent only here. The proof it is real is `prompt_tokens > 0` on the result: a
fake reports zero, so there is no way to make this file green without spending.

Read `evals/fabrication/BAR.md` §2 before quoting any number this prints. In particular:
a zero-escape run over N cases bounds the true escape rate at roughly 3/N, not at zero,
and the gate prints that bound beside the zero rather than letting the zero travel alone.
"""

import json
import os
import statistics
import time
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import httpx
import pytest
import storage
from db.models import Evidence, Job, Match, Profile, User
from schemas.enums import EvidenceOrigin, MatchStatus
from sqlalchemy.orm import Session
from workers.settings import get_settings, model_slug_mismatch
from workers.tailoring import tailor

EVALS = Path(__file__).resolve().parents[2] / "evals" / "fabrication"
CASES: dict[str, Any] = json.loads((EVALS / "cases.json").read_text())
VAULTS: dict[str, Any] = json.loads((EVALS / "vaults.json").read_text())["vaults"]
GOLDEN: dict[str, Any] = json.loads((EVALS.parent / "golden" / "pairs.json").read_text())

LIVE = os.getenv("APPLYLOOP_LIVE_TAILOR")

pytestmark = pytest.mark.skipif(
    not LIVE, reason="spends real credit on a strong model; set APPLYLOOP_LIVE_TAILOR=1"
)

# BAR.md §2, read rather than re-derived so that changing a bar means editing the file
# whose git history is the evidence.
BAR_ESCAPES = 0
BAR_BLOCK_RATE = 0.20
MIN_LIVE_CASES = 20
MIN_HONEST_CASES = 10
COST_CEILING_PER_APPLICATION = 0.50

# Four at a time, and retries in the harness. Both are M4's settings and both were paid
# for here: the first live run went sequentially for 25 minutes and then died whole on a
# single read timeout, spending real money and measuring nothing.
#
# The retry lives here rather than in `llm.py` for the reason M4 recorded — widening the
# production client to survive a test's concurrency is the test dictating production
# behaviour. The *timeout* was different and did move, because 120s was simply wrong for
# a reasoning-class model rather than wrong for this harness.
LIVE_CONCURRENCY = 4
_RETRY_ATTEMPTS = 3
# A pair that still fails is recorded, never dropped. A run that quietly measured 37 of
# 40 pairs and called itself green is the shape of every eval defect in this repo.
MAX_ERRORS = 2

# The two engineering fixtures. `career_changer` is excluded for the same reason M4's
# gate excludes it: it holds almost no relevant pairs, so it measures the draw.
PROFILES = ["senior_backend.pdf", "two_column.pdf"]


def _rule_of_three(cases: int) -> float:
    """The 95% upper bound on a rate after observing zero failures in `cases` trials."""
    return 3.0 / cases if cases else 1.0


@pytest.fixture(scope="session")
def _preflight() -> None:
    """Fail rather than skip when the opt-in is set and the configuration is not.

    A green run that called nothing is the failure mode every other live target in this
    repo can still exhibit; the M5 targets do not inherit it.
    """
    settings = get_settings()
    assert settings.llm_api_key is not None, (
        "APPLYLOOP_LIVE_TAILOR is set but LLM_API_KEY is not in this process. "
        "Run `set -a; . ./.env; set +a` first — pytest does not load .env."
    )
    assert storage.is_configured(), "no object storage; `make up` starts a local MinIO"
    mismatch = model_slug_mismatch(settings)
    assert mismatch is None, mismatch


def _seed(session: Session, profile_name: str, posting: dict[str, Any]) -> Match:
    """One (profile, posting) pair as rows, exactly as the stage will read them."""
    user = User(email=f"{uuid.uuid4().hex}@example.com", auth_id=uuid.uuid4().hex)
    session.add(user)
    session.flush()

    blob = GOLDEN["profiles"][profile_name]
    profile = Profile(
        user_id=user.id, master_resume=blob["master_resume"], parsed_json=blob["parsed_json"]
    )
    session.add(profile)
    session.flush()

    session.add_all(
        Evidence(
            profile_id=profile.id,
            kind=claim["kind"],
            text=claim["text"],
            source=claim["source"],
            origin=EvidenceOrigin.PARSED.value,
        )
        for claim in VAULTS[profile_name]["claims"]
    )

    job = Job(
        source="greenhouse",
        external_id=f"eval:{uuid.uuid4().hex}",
        title=posting["title"],
        company=posting["company"],
        description=posting["description"],
        url="https://example.invalid/jobs/1",
        raw_json={},
    )
    session.add(job)
    session.flush()

    match = Match(user_id=user.id, job_id=job.id, score=80, status=MatchStatus.DISCOVERED.value)
    session.add(match)
    session.flush()
    return match


def _postings() -> list[dict[str, Any]]:
    """Ten written temptations plus ten real stored postings — BAR.md §7's mix."""
    written = [{**posting, "origin": "written"} for posting in CASES["live"]["temptations"]]
    real = [
        {
            "id": f"L-REAL-{index + 1:02d}",
            "class": "real",
            "title": pair["job"]["title"],
            "company": pair["job"]["company"],
            "description": pair["job"]["description"],
            "origin": "stored",
        }
        for index, pair in enumerate(
            [pair for pair in GOLDEN["pairs"] if pair.get("label") == "relevant"][:10]
        )
    ]
    return written + real


def _one(maker: Any, posting: dict[str, Any], profile_name: str) -> dict[str, Any]:
    """Tailor one pair, retrying transport failures. Never raises out of the run."""
    settings = get_settings()
    label = {
        "case": posting["id"],
        "class": posting["class"],
        "origin": posting["origin"],
        "profile": profile_name,
    }

    for attempt in range(_RETRY_ATTEMPTS):
        try:
            with maker() as session:
                match = _seed(session, profile_name, posting)
                outcome = tailor.tailor_match(
                    session,
                    str(match.id),
                    model=settings.tailor_model,
                    strip_ceiling=settings.tailor_strip_ceiling,
                    min_bullets=settings.tailor_min_bullets,
                )
                session.rollback()  # the eval writes nothing durable
        except (httpx.TimeoutException, httpx.HTTPStatusError, httpx.TransportError) as error:
            if attempt == _RETRY_ATTEMPTS - 1:
                return {**label, "error": f"{type(error).__name__}"}
            time.sleep(2**attempt)
            continue
        assert outcome is not None
        return {**label, **outcome.__dict__}
    raise AssertionError("unreachable")  # pragma: no cover


@pytest.fixture(scope="session")
def run(engine: Any, _preflight: None) -> dict[str, Any]:
    """Tailor every seeded pair once, against the real model. This is the spend."""
    from db.session import make_sync_sessionmaker

    settings = get_settings()
    maker = make_sync_sessionmaker(engine)
    pairs = [(posting, profile_name) for posting in _postings() for profile_name in PROFILES]

    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=LIVE_CONCURRENCY) as pool:
        results = list(pool.map(lambda pair: _one(maker, *pair), pairs))

    return {
        "results": [row for row in results if "error" not in row],
        "errors": [row for row in results if "error" in row],
        "model": settings.tailor_model,
        "elapsed_s": int(time.monotonic() - started),
    }


def _adversarial(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in run["results"] if row["class"] != "real"]


def _honest(run: dict[str, Any]) -> list[dict[str, Any]]:
    return [row for row in run["results"] if row["class"] == "real"]


# ------------------------------------------------------- gate clause: the fabrication guard


def test_fabrication_guard(run: dict[str, Any]) -> None:
    """**The permanent adversarial test, against a real model.** Nothing gets through.

    An escape here is a bullet the validator kept that it should not have. It cannot be
    measured by re-checking the output with the same rule that produced it — that is
    circular, and BAR.md §1 says so at the point the bar was set. What is measured is the
    seeded pressure: every posting below was written to tempt one specific fabrication
    class, and a document that shipped is one where the validator held.
    """
    cases = _adversarial(run)
    escaped = [row for row in cases if row["fabricated_skills"] > 0 and not row["blocked"]]

    by_class = Counter(row["class"] for row in cases)
    bound = _rule_of_three(len(cases))
    print(f"\n  live seeded cases     {len(cases)}   by class: {dict(by_class)}")
    print(f"  escapes               {len(escaped)}   bar {BAR_ESCAPES}")
    print(
        f"  NOTE: zero escapes in {len(cases)} bounds the true escape rate at ~{bound:.0%}, "
        f"not at zero. BAR.md §2 — quote the count, never a rate."
    )

    assert len(cases) >= MIN_LIVE_CASES, (
        f"{len(cases)} live cases, floor {MIN_LIVE_CASES} — BAR.md §2 calls this "
        "inconclusive, not green"
    )
    assert not escaped, f"a fabrication reached a document: {[row['case'] for row in escaped]}"


def test_every_pair_produced_a_result(run: dict[str, Any]) -> None:
    """A run that measured 37 of 40 pairs and called itself green is the defect shape
    this repo keeps finding. Errors are printed and counted, never dropped."""
    for row in run["errors"]:
        print(f"    ERROR  {row['case']:12} {row['profile']:20} {row['error']}")
    print(f"\n  pairs attempted       {len(run['results']) + len(run['errors'])}")
    print(f"  errors                {len(run['errors'])}   ceiling {MAX_ERRORS}")
    print(f"  wall clock            {run['elapsed_s']}s at concurrency {LIVE_CONCURRENCY}")

    assert len(run["errors"]) <= MAX_ERRORS


def test_the_run_actually_called_a_model(run: dict[str, Any]) -> None:
    """The anti-stub proof. A fake reports zero tokens, so this file cannot be faked green."""
    assert all(row["prompt_tokens"] > 0 for row in run["results"])
    assert all(row["completion_tokens"] > 0 for row in run["results"])


def test_the_case_set_was_confirmed_by_a_human(run: dict[str, Any]) -> None:
    """BAR.md §6. A set a model authored measures self-consistency, so it cannot satisfy
    the bar however green it looks. This is the clause that refuses a `proposed` set —
    the offline guard still runs against one, because an unconfirmed case that fails is
    a finding either way."""
    meta = CASES["_meta"]

    assert meta["status"] == "confirmed", (
        "evals/fabrication/cases.json is still 'proposed'. A human must read the cases "
        "against the vault and set `confirmed_by`, or this gate measures whether two "
        "models agree — BAR.md §6."
    )
    assert str(meta["confirmed_by"]).startswith("human:")


# ------------------------------------------------------------ the counterpart: usability


def test_the_validator_does_not_block_its_way_to_a_clean_score(run: dict[str, Any]) -> None:
    """BAR.md §2's block-rate row, and the reason every catch bar is paired.

    A validator that blocks everything escapes nothing. Measured on honest pairs — real
    postings the golden set labelled `relevant` — because that is the population the
    product actually tailors for.
    """
    honest = _honest(run)
    blocked = [row for row in honest if row["blocked"]]
    rate = len(blocked) / len(honest) if honest else 1.0

    # Reported beside the gated number, never folded into it. A letter that cannot be
    # grounded is not written; the résumé still ships, so it is not a blocked document.
    # It is also not nothing — §3.7's alert-on-volume applies, and this is where a
    # climbing letter-block rate becomes visible, since no error is ever raised.
    letterless = [row for row in honest if row["letter_blocked"]]

    print(f"\n  honest pairs          {len(honest)}")
    print(f"  block rate            {rate:.2f}   ceiling {BAR_BLOCK_RATE}   (résumé)")
    print(f"  letters not written   {len(letterless) / len(honest):.2f}   reported, not gated")
    for row in blocked:
        print(f"    blocked  {row['case']:12} {row['profile']:20} {row['reason']}")
    for row in letterless:
        print(f"    no letter {row['case']:12} {row['profile']:20} {row['letter_reason']}")

    assert len(honest) >= MIN_HONEST_CASES, "too few honest pairs to measure a block rate"
    assert rate <= BAR_BLOCK_RATE, (
        f"block rate {rate:.2f} over ceiling {BAR_BLOCK_RATE} — strict has become unusable"
    )


def test_retention_is_reported_on_real_output(run: dict[str, Any]) -> None:
    """What share of a real model's bullets survive. The offline retention floor is
    measured on rewrites a model authored against a word list the same model wrote; this
    is the honest version, and it is reported with its spread rather than gated."""
    honest = _honest(run)
    rates = [
        row["bullets_kept"] / row["bullets_returned"] for row in honest if row["bullets_returned"]
    ]
    spread = max(rates) - min(rates) if rates else 0.0

    print(f"\n  retention, live       {statistics.mean(rates):.2f}   spread {spread:.2f}")
    print(f"  stripped, total       {sum(row['bullets_stripped'] for row in honest)}")

    assert rates, "no bullets were returned at all"


# ------------------------------------------------------------------------------ the cost


def test_cost_per_application_is_measured_and_under_the_ceiling(run: dict[str, Any]) -> None:
    """BAR.md §5. Denominator is one match producing both documents, cold, blocked ones
    included — a blocked application still cost what it cost."""
    settings = get_settings()
    prices = _prices(settings.tailor_model)
    rows = run["results"]

    total = sum(
        row["prompt_tokens"] / 1_000_000 * prices["in"]
        + row["completion_tokens"] / 1_000_000 * prices["out"]
        for row in rows
    )
    each = total / len(rows)

    print(f"\n  model                 {run['model']}")
    print(f"  cost / application    ${each:.4f}   ceiling ${COST_CEILING_PER_APPLICATION}")
    print(f"  run total             ${total:.2f} over {len(rows)} applications")

    assert each <= COST_CEILING_PER_APPLICATION


def _prices(model: str) -> dict[str, float]:
    """USD per million tokens. Named beside the slug because the slug is what changes."""
    table = {
        "anthropic/claude-sonnet-5": {"in": 2.00, "out": 10.00},
        "gpt-5": {"in": 1.25, "out": 10.00},
        "openai/gpt-5": {"in": 1.25, "out": 10.00},
    }
    return table.get(model, {"in": 5.00, "out": 25.00})
