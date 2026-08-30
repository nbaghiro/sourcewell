import pytest
from httpx import AsyncClient

from app.core.config import Settings


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


@pytest.mark.db
async def test_every_new_org_gets_a_default_workspace(db_client: AsyncClient) -> None:
    """An org with no workspace is an account that can't do anything — `require_workspace`
    rejects every scoped request — so creating one is part of creating an organization, not
    something each caller remembers to do afterwards.

    This used to be bolted on by two of the three paths that create an org and skipped by the
    third, so a bootstrap through this endpoint landed its admin in a dead tenant.
    """
    uid = await _signup(db_client, "initech")
    listed = await db_client.get("/workspaces", headers={"X-User-Id": uid})
    assert listed.status_code == 200
    assert [w["name"] for w in listed.json()] == ["Default workspace"]


@pytest.mark.db
async def test_org_bootstrap_is_refused_outside_local(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """This endpoint takes no password and no confirmation, so in production it was an anonymous
    writer of `User` rows carrying any address the caller named. Real accounts come from
    `POST /auth/signup` or an OAuth sign-in; teammates come from an invitation."""
    monkeypatch.setattr("app.api.tenancy.get_settings", lambda: Settings(environment="production"))
    r = await db_client.post(
        "/organizations",
        json={
            "org_name": "Evil Corp",
            "slug": "evil",
            "admin_email": "someone@elsewhere.com",
            "admin_name": "Nobody",
        },
    )
    assert r.status_code == 404
