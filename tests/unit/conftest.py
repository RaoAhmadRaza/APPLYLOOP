"""Keep the developer's `.env` out of the unit suite's environment.

The parent `conftest.py` nulls `Settings.model_config["env_file"]` so that an interlock
test — "the aggregator no-ops without a proxy", "the mirror refuses without credentials" —
is asserting on a genuinely absent setting. That is necessary and it is not sufficient,
for a reason that took three separate failures to find:

**`markitdown` imports `magika`, and `magika/__init__.py` calls
`dotenv.load_dotenv(dotenv.find_dotenv())` at import time.** It walks up from the working
directory, finds this repo's `.env`, and copies every value into `os.environ` for the
whole process. Nulling `env_file` does nothing about that, because pydantic-settings reads
`os.environ` *first* — the file is the fallback, not the source.

It is ordering-dependent, which is why it looked like a mystery: nothing leaks until some
test imports `markitdown`, and then every later test in that process sees the developer's
real configuration. `tests/unit/test_drive.py` passed alone and failed in the suite;
`test_llm.py` did the same the day a cross-field settings check was briefly added.

So the keys named in `.env` are removed for the duration of each unit test and put back
afterwards. Scoped to `tests/unit` on purpose: the live integration suites are *given*
that environment deliberately, by `make verify-live-*`, and must keep it.
"""

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

ENV_FILE = Path(__file__).resolve().parents[2] / ".env"

# The parent conftest exports these itself, pointing at the throwaway containers. Removing
# them would break every test that touches a database rather than making one hermetic.
KEPT = frozenset({"DATABASE_URL", "REDIS_URL"})


def _declared_keys() -> frozenset[str]:
    """Every key name `.env` declares. Names only — no value is ever read here."""
    if not ENV_FILE.exists():
        return frozenset()
    names = set()
    for line in ENV_FILE.read_text().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            names.add(stripped.split("=", 1)[0].strip())
    return frozenset(names - KEPT)


DECLARED = _declared_keys()


@pytest.fixture(autouse=True)
def _no_dotenv_in_environ() -> Iterator[None]:
    """Remove `.env`'s keys from `os.environ` around every unit test.

    Function-scoped so it also covers whatever a *later* import drags in, and restored
    afterwards so nothing outside the unit suite is affected.
    """
    removed = {key: os.environ.pop(key) for key in DECLARED if key in os.environ}
    yield
    os.environ.update(removed)
