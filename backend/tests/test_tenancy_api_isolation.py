"""End-to-end isolation + authorization tests through the API."""

import pytest
from httpx import AsyncClient


async def _signup(client: AsyncClient, slug: str) -> str:
    r = await client.post(
        "/organizations",
        json={
            "org_name": f"Org {slug}",
            "slug": slug,
            "admin_email": f"admin@{slug}.com",
            "admin_name": "Admin",
        },
    )
    assert r.status_code == 201
    uid = r.json()["admin_user_id"]
    assert isinstance(uid, str)
    return uid


@pytest.mark.db
async def test_cross_org_workspace_isolation(db_client: AsyncClient) -> None:
    a_admin = await _signup(db_client, "orga")
    b_admin = await _signup(db_client, "orgb")

    created = await db_client.post(
        "/workspaces", json={"name": "A WS"}, headers={"X-User-Id": a_admin}
    )
    wa_id = created.json()["id"]

    # Org B cannot see Org A's workspace in the list...
    listed_b = await db_client.get("/workspaces", headers={"X-User-Id": b_admin})
    assert wa_id not in [w["id"] for w in listed_b.json()]

    # ...nor fetch it by id.
    got = await db_client.get(f"/workspaces/{wa_id}", headers={"X-User-Id": b_admin})
    assert got.status_code == 404


@pytest.mark.db
async def test_member_cannot_create_workspace(db_client: AsyncClient) -> None:
    admin = await _signup(db_client, "orgc")
    admin_h = {"X-User-Id": admin}

    ws = await db_client.post("/workspaces", json={"name": "W"}, headers=admin_h)
    ws_id = ws.json()["id"]
    user = await db_client.post(
        "/users", json={"email": "m@orgc.com", "name": "Member"}, headers=admin_h
    )
    user_id = user.json()["id"]
    await db_client.post(
        "/memberships",
        json={"user_id": user_id, "scope": "workspace", "role": "member", "workspace_id": ws_id},
        headers=admin_h,
    )

    # The member is not an org admin → cannot create workspaces.
    denied = await db_client.post("/workspaces", json={"name": "X"}, headers={"X-User-Id": user_id})
    assert denied.status_code == 403
