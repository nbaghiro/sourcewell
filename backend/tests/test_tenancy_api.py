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
async def test_signup_and_me(db_client: AsyncClient) -> None:
    uid = await _signup(db_client, "acme")
    me = await db_client.get("/me", headers={"X-User-Id": uid})
    assert me.status_code == 200
    body = me.json()
    assert body["user_id"] == uid
    assert body["is_org_admin"] is True


@pytest.mark.db
async def test_create_and_list_workspaces(db_client: AsyncClient) -> None:
    uid = await _signup(db_client, "globex")
    headers = {"X-User-Id": uid}
    created = await db_client.post(
        "/workspaces", json={"name": "Client A", "kind": "client"}, headers=headers
    )
    assert created.status_code == 201
    ws_id = created.json()["id"]

    listed = await db_client.get("/workspaces", headers=headers)
    assert listed.status_code == 200
    assert ws_id in [w["id"] for w in listed.json()]


@pytest.mark.db
async def test_me_requires_auth(db_client: AsyncClient) -> None:
    assert (await db_client.get("/me")).status_code == 401
