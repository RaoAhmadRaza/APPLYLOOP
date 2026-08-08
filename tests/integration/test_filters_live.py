"""BAR.md §2's pool floor and per-filter cap, against the **real** open pool.

These two clauses used to live in `test_matching_live.py`, and they could never pass
there. §7 requires a golden run to seed only the stored job payloads — so that a pair
cannot rot when `close_missing` or a dedupe promotion removes a job from production — and
that makes the per-profile pool 16 rows *by construction*. A floor of 200 against a pool
of 16 is a failure the matcher cannot fix and a green run cannot earn.

The clauses themselves are worth keeping, and they are the reason M4 exists in the shape
it does: an exact-array location filter once left every fixture profile with at most three
candidates out of 1,458, which produces excellent precision over nothing at all and passes
every other clause of the gate. That defect is visible only against a real pool, which is
what this file runs against.

Read-only. It inserts nothing and commits nothing: `ProfileRead` is constructed in memory
from the golden set's stored profiles, and the synthetic `user_id` means `_already_scored`
matches no rows. Safe to point at production.
"""

import json
import os
import pathlib
import uuid
from datetime import UTC, datetime
from typing import Any

import pytest
from db.session import make_sync_engine
from schemas.prefs import Prefs
from schemas.profile import ProfileRead
from sqlalchemy.orm import Session
from workers.matching import filters

PAIRS = pathlib.Path(__file__).parents[2] / "evals" / "golden" / "pairs.json"

# BAR.md §2, scoped here rather than to the golden harness. See the module docstring.
POOL_FLOOR = 200
PER_FILTER_CAP = 0.70

pytestmark = [
    pytest.mark.skipif(
        not os.getenv("APPLYLOOP_LIVE_POOL"),
        reason="reads the real job pool; set APPLYLOOP_LIVE_POOL=1",
    ),
    pytest.mark.skipif(
        not os.getenv("DATABASE_URL"),
        reason="needs a database holding the real pool",
    ),
]


def _profiles() -> dict[str, Any]:
    if not PAIRS.exists():
        pytest.skip("evals/golden/pairs.json not built yet")
    profiles: dict[str, Any] = json.loads(PAIRS.read_text())["profiles"]
    return profiles


def _view(blob: dict[str, Any]) -> ProfileRead:
    """The four promoted columns the filters read, and nothing else.

    A synthetic `user_id` on purpose: `filters._already_scored` looks for `matches` rows
    belonging to it, and a fresh UUID has none — so the funnel measures the filters rather
    than whatever the real matcher happens to have scored already.
    """
    now = datetime.now(UTC)
    return ProfileRead.model_validate(
        {
            "id": uuid.uuid4(),
            "user_id": uuid.uuid4(),
            "created_at": now,
            "updated_at": now,
            "master_resume": blob["master_resume"],
            "parsed_json": blob["parsed_json"],
            "prefs_json": {},
            "locations": blob["locations"],
            "seniority": blob["seniority"],
            "work_auth": blob["work_auth"],
        }
    )


@pytest.fixture(scope="session")
def funnels() -> dict[str, dict[str, int]]:
    """One filter pass per fixture profile over the live pool."""
    engine = make_sync_engine(os.environ["DATABASE_URL"])
    out: dict[str, dict[str, int]] = {}
    with Session(engine) as session:
        for fixture, blob in _profiles().items():
            view = _view(blob)
            _, funnel = filters.candidates(session, view, Prefs())
            out[fixture] = funnel.as_payload()
            print(f"\n  {fixture}: {out[fixture]}")
    engine.dispose()
    return out


def test_the_pool_itself_is_not_empty(funnels: dict[str, dict[str, int]]) -> None:
    """The failure mode that makes every other number here meaningless.

    A pool of zero passes a per-filter cap trivially and fails the floor for a reason that
    has nothing to do with the filters — most likely `registry.SEED` never having been run,
    which is exactly what happened on 2026-08-08 and left one employer at 55% of the pool.
    """
    for fixture, funnel in funnels.items():
        assert funnel["pool"] > 0, (
            f"{fixture}: the open pool is empty — run `make seed && make ingest`"
        )


def test_the_filters_did_not_quietly_empty_the_pool(funnels: dict[str, dict[str, int]]) -> None:
    """BAR.md §2's pool floor. The filters must leave a usable candidate set."""
    for fixture, funnel in funnels.items():
        assert funnel["candidates"] >= POOL_FLOOR, (
            f"{fixture} left {funnel['candidates']} candidates of {funnel['pool']}"
        )


def test_no_single_filter_drops_almost_everything(funnels: dict[str, dict[str, int]]) -> None:
    """A filter that drops everything and one that drops nothing are the same defect."""
    for fixture, funnel in funnels.items():
        pool = funnel["pool"]
        for name, dropped in funnel.items():
            if name.startswith("dropped_"):
                assert dropped <= PER_FILTER_CAP * pool, f"{fixture}: {name} took {dropped}/{pool}"
