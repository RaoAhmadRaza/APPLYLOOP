"""The M0 card's other half: basic row CRUD across every table.

Builds a real FK chain (user -> profile, job -> match -> document/application/approval)
rather than testing tables in isolation, because a CASCADE or a FK typo only shows up
when the rows actually reference each other.
"""

from typing import Any

import pytest
from httpx import AsyncClient


async def _create(client: AsyncClient, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    resp = await client.post(path, json=payload)
    assert resp.status_code == 201, f"POST {path} -> {resp.status_code}: {resp.text}"
    return resp.json()


@pytest.fixture
async def chain(client: AsyncClient) -> dict[str, Any]:
    user = await _create(client, "/users", {"email": "chain@example.com", "auth_id": "auth|chain"})
    profile = await _create(
        client,
        "/profiles",
        {
            "user_id": user["id"],
            "work_auth": "citizen",
            "seniority": "senior",
            "locations": ["Berlin", "Remote"],
            "salary_floor": 90_000,
        },
    )
    job = await _create(
        client,
        "/jobs",
        {
            "source": "lever",
            "external_id": "lv-1",
            "title": "Engineer",
            "company": "Acme",
            "url": "https://jobs.lever.co/acme/1",
            "raw_json": {},
        },
    )
    match = await _create(client, "/matches", {"user_id": user["id"], "job_id": job["id"]})
    return {"user": user, "profile": profile, "job": job, "match": match}


async def test_companies_crud(client: AsyncClient) -> None:
    created = await _create(
        client,
        "/companies",
        {"name": "Acme", "domain": "acme.test", "ats_type": "greenhouse", "ats_slug": "acme"},
    )
    assert created["status"] == "active"

    patched = await client.patch(f"/companies/{created['id']}", json={"status": "retired"})
    assert patched.status_code == 200
    assert patched.json()["status"] == "retired"
    # PATCH must not null the fields it wasn't given.
    assert patched.json()["name"] == "Acme"

    assert (await client.delete(f"/companies/{created['id']}")).status_code == 204
    assert (await client.get(f"/companies/{created['id']}")).status_code == 404


async def test_full_fk_chain_creates(chain: dict[str, Any]) -> None:
    assert chain["profile"]["user_id"] == chain["user"]["id"]
    assert chain["match"]["job_id"] == chain["job"]["id"]
    # A fresh match starts at the first state of the §6.1 machine.
    assert chain["match"]["status"] == "discovered"


async def test_document_application_approval_hang_off_a_match(
    client: AsyncClient, chain: dict[str, Any]
) -> None:
    match_id = chain["match"]["id"]

    doc = await _create(
        client,
        "/documents",
        {"match_id": match_id, "type": "resume", "storage_url": "s3://bucket/r.pdf"},
    )
    assert doc["version"] == 1

    application = await _create(
        client, "/applications", {"match_id": match_id, "method": "extension"}
    )
    assert application["status"] == "pending"
    assert application["submitted_at"] is None, "submitted_at must not be forged by a default"

    approval = await _create(client, "/approvals", {"match_id": match_id, "channel": "telegram"})
    assert approval["decided_at"] is None


async def test_events_use_an_integer_key(client: AsyncClient, chain: dict[str, Any]) -> None:
    event = await _create(
        client,
        "/events",
        {"user_id": chain["user"]["id"], "type": "m0.smoke", "payload_json": {"ok": True}},
    )
    assert isinstance(event["id"], int)
    assert (await client.get(f"/events/{event['id']}")).status_code == 200


async def test_rejects_a_value_outside_the_check_constraint(client: AsyncClient) -> None:
    """`ats_type` is bounded. FastAPI validates against the enum before the DB sees it,
    so this is a 422 — the CHECK is the second line of defence, covered in
    test_constraints.py."""
    resp = await client.post(
        "/companies", json={"name": "X", "ats_type": "workday", "ats_slug": "x"}
    )
    assert resp.status_code == 422


async def test_every_table_lists(client: AsyncClient, chain: dict[str, Any]) -> None:
    for path in (
        "/companies",
        "/users",
        "/profiles",
        "/jobs",
        "/matches",
        "/documents",
        "/applications",
        "/approvals",
        "/events",
    ):
        resp = await client.get(path, params={"limit": 1})
        assert resp.status_code == 200, f"GET {path} -> {resp.status_code}"
        assert "items" in resp.json()
