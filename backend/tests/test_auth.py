"""LinkedIn (Unipile hosted-auth) sign-in: the sealed session + notify→provision→finish flow."""

import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
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


# --- dev-login still works when LinkedIn auth is unconfigured -----------------


@pytest.mark.db
async def test_dev_login_endpoint_still_available(db_client: AsyncClient) -> None:
    resp = await db_client.post("/auth/dev-login", json={})
    assert resp.status_code == 200
    assert resp.json()["user"]["email"]  # one-click demo sign-in (Unipile not configured in tests)
