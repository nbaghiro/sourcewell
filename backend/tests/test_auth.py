"""LinkedIn (Unipile hosted-auth) sign-in: the sealed session + notify→provision→finish flow."""

import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import hash_password
from app.models import LoginAttempt, User
from app.services.workspace import auth
from tests.factories import make_org, make_user

_DSN = "https://api1.unipile.com:1234"


# --- the sealed session ------------------------------------------------------


@pytest.mark.db
async def test_session_mint_and_resolve(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="auth-sess")
    user = await make_user(db_session, org=org)
    sealed = auth.mint_session(user.id)
    assert await auth._session_user_id(db_session, sealed) == user.id
    assert await auth._session_user_id(db_session, "garbage") is None  # bad cookie → no user


# --- the LinkedIn sign-in flow -----------------------------------------------


@pytest.mark.db
async def test_notify_provisions_then_finish_returns_user(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.ext.unipile.get_settings",
        lambda: Settings(unipile_api_key="k", unipile_dsn=_DSN),
    )
    db_session.add(LoginAttempt(state="s1", status="pending"))
    await db_session.flush()

    with respx.mock:
        respx.get(f"{_DSN}/api/v1/users/me").mock(
            return_value=Response(
                200, json={"member_urn": "urn:li:99", "first_name": "Mei", "last_name": "T"}
            )
        )
        await auth.complete_linkedin_notify(db_session, state="s1", account_id="acct-1")

    user = (
        await db_session.execute(select(User).where(User.sso_subject == "urn:li:99"))
    ).scalar_one()
    assert user.name == "Mei T"  # provisioned from the LinkedIn profile

    uid = await auth.finish_linkedin_login(db_session, state="s1")
    assert uid == user.id
    assert await auth.finish_linkedin_login(db_session, state="s1") is None  # consumed once


@pytest.mark.db
async def test_finish_pending_returns_none(db_session: AsyncSession) -> None:
    db_session.add(LoginAttempt(state="pend", status="pending"))
    await db_session.flush()
    assert await auth.finish_linkedin_login(db_session, state="pend") is None  # notify not in yet


# --- email/password login (generic: verifies the user's stored hash) ---------


async def _seed_password_user(session: AsyncSession, *, slug: str) -> str:
    org = await make_org(session, slug=slug)
    session.add(
        User(
            organization_id=org.id,
            email="agent@acme.test",
            name="Agent",
            password_hash=hash_password("testpass"),
        )
    )
    await session.flush()
    return "agent@acme.test"


@pytest.mark.db
async def test_password_login_succeeds_against_a_seeded_user(
    db_session: AsyncSession, db_client: AsyncClient
) -> None:
    email = await _seed_password_user(db_session, slug="pw-ok")
    resp = await db_client.post("/auth/password", json={"email": email, "password": "testpass"})
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == email


@pytest.mark.db
async def test_password_login_rejects_wrong_password(
    db_session: AsyncSession, db_client: AsyncClient
) -> None:
    email = await _seed_password_user(db_session, slug="pw-bad")
    resp = await db_client.post("/auth/password", json={"email": email, "password": "nope"})
    assert resp.status_code == 401


@pytest.mark.db
async def test_password_login_rejects_unknown_email(db_client: AsyncClient) -> None:
    resp = await db_client.post("/auth/password", json={"email": "nobody@x.test", "password": "x"})
    assert resp.status_code == 401
