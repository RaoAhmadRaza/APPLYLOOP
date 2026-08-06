"""M0 gate item 4: POST /jobs -> GET /jobs/{id} round-trips."""

from datetime import datetime
from typing import Any

from httpx import AsyncClient

PAYLOAD: dict[str, Any] = {
    "source": "greenhouse",
    "external_id": "gh-4242",
    "title": "Senior Backend Engineer",
    "company": "Acme",
    "location": "Remote — EU",
    "locations": ["Berlin", "Remote — EU"],
    "remote_mode": "hybrid",
    "description": "Build the thing.",
    "url": "https://boards.greenhouse.io/acme/jobs/4242",
    "ats_type": "greenhouse",
    "posted_at": "2026-08-01T09:00:00+00:00",
    "raw_json": {"id": 4242, "absolute_url": "https://boards.greenhouse.io/acme/jobs/4242"},
}


async def test_post_then_get_round_trips_every_field(client: AsyncClient) -> None:
    # Act
    created = await client.post("/jobs", json=PAYLOAD)
    assert created.status_code == 201, created.text
    job_id = created.json()["id"]

    fetched = await client.get(f"/jobs/{job_id}")

    # Assert
    assert fetched.status_code == 200
    body = fetched.json()
    for field, expected in PAYLOAD.items():
        if field == "posted_at":
            # Compare instants, not strings: pydantic serialises UTC as "Z" while the
            # payload used "+00:00". Same moment, different spelling.
            assert datetime.fromisoformat(body[field]) == datetime.fromisoformat(expected)
            continue
        assert body[field] == expected, f"{field} did not round-trip"


async def test_server_assigns_a_uuidv7_id_and_timestamps(client: AsyncClient) -> None:
    body = (await client.post("/jobs", json={**PAYLOAD, "external_id": "gh-ids"})).json()

    import uuid

    parsed = uuid.UUID(body["id"])
    assert parsed.version == 7, f"expected Postgres 18 uuidv7(), got v{parsed.version}"
    assert body["created_at"] is not None
    assert body["updated_at"] is not None


async def test_unknown_id_is_404(client: AsyncClient) -> None:
    missing = "01890000-0000-7000-8000-000000000000"
    assert (await client.get(f"/jobs/{missing}")).status_code == 404


async def test_duplicate_source_external_id_is_409_not_500(client: AsyncClient) -> None:
    """The ingest dedupe key surfacing correctly at the HTTP boundary. A duplicate is
    the database working as designed, not a server fault."""
    payload = {**PAYLOAD, "external_id": "gh-dupe"}
    assert (await client.post("/jobs", json=payload)).status_code == 201
    assert (await client.post("/jobs", json=payload)).status_code == 409


async def test_list_is_bounded(client: AsyncClient) -> None:
    over_limit = await client.get("/jobs", params={"limit": 10_000})
    assert over_limit.status_code == 422, "list endpoint must refuse an unbounded page"

    ok = await client.get("/jobs", params={"limit": 5})
    assert ok.status_code == 200
    body = ok.json()
    assert set(body) == {"items", "total", "limit", "offset"}
    assert len(body["items"]) <= 5
