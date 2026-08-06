"""Layer 1 sourcing: direct ATS public JSON (§4.1).

§5.2 makes the ATS adapter one of three deliberately plural boundaries. It is expressed
here as a **module per provider**, not a class hierarchy — same reasoning §5.2 gives for
refusing an `ApplyStrategy` ABC. `Adapter` below is a typing Protocol, so it costs
nothing at runtime and still checks all six implementations against one shape.

Two conventions every adapter follows, both load-bearing:

1. **`external_id` is always `f"{slug}:{native_id}"`.** Greenhouse integers and
   Lever/Ashby UUIDs are provably unique across tenants. Recruitee's integer `id` and
   Workable's `shortcode` are not. A collision there would make
   `uq_jobs_source_external_id` overwrite one employer's posting with another's — the
   constraint causing corruption rather than preventing it. Prefixing removes the
   question for all six at the cost of nothing; the bare id stays in `raw_json`.

2. **`normalize()` is pure.** It takes a payload and returns a `JobCreate`, touching no
   network and no database. That is what makes the whole layer unit-testable against
   recorded fixtures.

`fetch_detail`/`native_id` exist only on SmartRecruiters, the one provider that
withholds descriptions from its list response. Callers probe with `getattr`.
"""

from types import ModuleType
from typing import Any, Protocol

import httpx
from schemas.enums import AtsType
from schemas.job import JobCreate

from workers.scraping.adapters import (
    ashby,
    greenhouse,
    lever,
    recruitee,
    smartrecruiters,
    workable,
)


class Adapter(Protocol):
    """The shape §5.2 names as `fetch(slug) -> list[RawPosting]`.

    No `RawPosting` type is introduced: `JobCreate` already is the normalized wire
    format, and a second near-identical model would be one more thing to keep in sync.
    """

    SOURCE: str
    ATS: AtsType

    def fetch(self, client: httpx.Client, slug: str) -> list[dict[str, Any]]: ...

    def normalize(self, raw: dict[str, Any], company: str, slug: str) -> JobCreate: ...


ADAPTERS: dict[AtsType, ModuleType] = {
    AtsType.GREENHOUSE: greenhouse,
    AtsType.LEVER: lever,
    AtsType.ASHBY: ashby,
    AtsType.WORKABLE: workable,
    AtsType.SMARTRECRUITERS: smartrecruiters,
    AtsType.RECRUITEE: recruitee,
}
