"""LinkedIn (Unipile hosted-auth) sign-in: the sealed session + notify→provision→finish flow."""

import pytest
import respx
from httpx import AsyncClient, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import hash_password
from app.models import (
    Connection,
    ConnectionProvider,
    LoginAttempt,
    MembershipRole,
    MembershipScope,
    User,
)
from app.services.workspace import auth
from app.services.workspace.connections import seat_account_id
from tests.factories import make_membership, make_org, make_user

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


# --- WorkOS SSO authorization URL (per-IdP deep-linking) ---------------------

_WOS = Settings(
    workos_api_key="sk_test_x", workos_client_id="client_x", session_cookie_password="unit-test"
)


def test_workos_login_url_deep_links_per_idp(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.workspace.auth.get_settings", lambda: _WOS)
    auth._workos_client.cache_clear()
    assert "provider=authkit" in (auth.workos_login_url() or "")  # generic SSO chooser
    assert "provider=GoogleOAuth" in (auth.workos_login_url(idp="google") or "")
    assert "provider=MicrosoftOAuth" in (auth.workos_login_url(idp="microsoft") or "")
    # an unknown hint falls back to the AuthKit chooser, never an arbitrary provider
    assert "provider=authkit" in (auth.workos_login_url(idp="bogus") or "")


def test_workos_login_url_none_when_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.workspace.auth.get_settings",
        lambda: Settings(workos_api_key="", workos_client_id=""),
    )
    auth._workos_client.cache_clear()
    assert auth.workos_login_url(idp="google") is None


# --- LinkedIn seat connect (Settings: hosted-auth for an already-signed-in user) ---------------

_CONNECT_SETTINGS = Settings(
    unipile_api_key="k", unipile_dsn=_DSN, unipile_webhook_secret="wh", session_cookie_password="x"
)


@pytest.mark.db
async def test_start_seat_connect_creates_pending_attempt_for_user(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.ext.unipile.get_settings", lambda: _CONNECT_SETTINGS)
    monkeypatch.setattr("app.services.workspace.auth.get_settings", lambda: _CONNECT_SETTINGS)
    org = await make_org(db_session, slug="seat-connect")
    user = await make_user(db_session, org=org)

    with respx.mock:
        respx.post(f"{_DSN}/api/v1/hosted/accounts/link").mock(
            return_value=Response(200, json={"url": "https://wizard"})
        )
        url = await auth.start_seat_connect(db_session, user_id=user.id)

    assert url == "https://wizard"
    # The attempt is pre-named with the user — that's what flags it as a connect, not a sign-in.
    attempt = (
        await db_session.execute(select(LoginAttempt).where(LoginAttempt.user_id == user.id))
    ).scalar_one()
    assert attempt.status == "pending"


@pytest.mark.db
async def test_connect_notify_attaches_seat_without_provisioning(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.ext.unipile.get_settings", lambda: _CONNECT_SETTINGS)
    org = await make_org(db_session, slug="seat-notify")
    user = await make_user(db_session, org=org)
    db_session.add(LoginAttempt(state="cs1", status="pending", user_id=user.id))
    await db_session.flush()

    with respx.mock:
        respx.get(f"{_DSN}/api/v1/users/me").mock(
            return_value=Response(
                200, json={"member_urn": "urn:li:zzz", "first_name": "Pat", "last_name": "Lee"}
            )
        )
        await auth.complete_linkedin_notify(db_session, state="cs1", account_id="acct-seat")

    # Seat attached to the *existing* user — not a fresh member_urn-keyed account.
    assert (
        await seat_account_id(db_session, user_id=user.id, provider=ConnectionProvider.linkedin)
        == "acct-seat"
    )
    assert user.sso_subject != "urn:li:zzz"  # not re-keyed to the LinkedIn identity
    # Connect has no browser-side finish, so the attempt is consumed here.
    assert (
        await db_session.execute(select(LoginAttempt).where(LoginAttempt.state == "cs1"))
    ).scalar_one_or_none() is None
    # The connected identity surfaces as the seat's display name.
    seat = (
        await db_session.execute(
            select(Connection).where(
                Connection.user_id == user.id, Connection.provider == ConnectionProvider.linkedin
            )
        )
    ).scalar_one()
    assert seat.capabilities.get("display_name") == "Pat Lee"


@pytest.mark.db
async def test_connect_endpoint_guards_email_and_unconfigured(
    db_session: AsyncSession, db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Force Unipile unconfigured so the endpoint resolves the guards without touching the network.
    monkeypatch.setattr(
        "app.ext.unipile.get_settings", lambda: Settings(unipile_api_key="", unipile_dsn="")
    )
    org = await make_org(db_session, slug="connect-ep")
    user = await make_user(db_session, org=org)
    await make_membership(
        db_session,
        user=user,
        org=org,
        scope=MembershipScope.organization,
        role=MembershipRole.org_admin,
    )
    headers = {"X-User-Id": user.id}
    # Email seats aren't available yet → 501, not a silent stub success.
    gmail = await db_client.post("/settings/connections/gmail/connect", headers=headers)
    assert gmail.status_code == 501
    # LinkedIn is real, but Unipile is unconfigured here → 503 (again, never a stub success).
    linkedin = await db_client.post("/settings/connections/linkedin/connect", headers=headers)
    assert linkedin.status_code == 503
