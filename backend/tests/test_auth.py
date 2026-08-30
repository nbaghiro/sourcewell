"""The sealed session and the pinned OAuth buttons — the two ways into Sourcewell."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import hash_password
from app.models import User
from app.services.workspace import auth
from app.services.workspace import connections as connections_service
from tests.factories import make_org, make_user

_DSN = "https://api1.unipile.com:1234"


# --- the sealed session ------------------------------------------------------


@pytest.mark.db
async def test_session_mint_and_resolve(db_session: AsyncSession) -> None:
    user = await make_user(db_session)
    sealed = auth.mint_session(user.id)
    assert await auth._session_user_id(db_session, sealed) == user.id
    assert await auth._session_user_id(db_session, "garbage") is None  # bad cookie → no user


# --- the OAuth buttons are pinned to a real provider --------------------------


def test_only_google_and_microsoft_are_offered() -> None:
    """AuthKit's `provider="authkit"` picker also offers enterprise SSO and an email magic-link,
    so a button wired to it didn't go where its label said. Anything outside the map is refused
    outright rather than quietly falling back to the picker."""
    assert set(auth.WORKOS_PROVIDERS) == {"google", "microsoft"}
    assert auth.WORKOS_PROVIDERS["google"] == "GoogleOAuth"
    assert auth.WORKOS_PROVIDERS["microsoft"] == "MicrosoftOAuth"


def test_unknown_provider_has_no_url(monkeypatch: pytest.MonkeyPatch) -> None:
    configured = Settings(
        workos_api_key="k", workos_client_id="c", session_cookie_password="p" * 44
    )
    monkeypatch.setattr("app.services.workspace.auth.get_settings", lambda: configured)
    assert auth.workos_login_url("authkit") is None
    assert auth.workos_login_url("linkedin") is None


def test_no_url_when_workos_is_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.services.workspace.auth.get_settings", Settings)
    assert auth.workos_login_url("google") is None


# --- connecting a LinkedIn seat needs the whole flow configured ---------------


@pytest.mark.db
async def test_seat_connect_requires_the_whole_flow_to_be_configured(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A wizard whose notify hop is disabled would leave the user with nowhere to report back."""

    def _half_configured() -> Settings:
        return Settings(unipile_api_key="k", unipile_dsn=_DSN, session_cookie_password="c" * 44)

    monkeypatch.setattr("app.services.workspace.connections.get_settings", _half_configured)
    monkeypatch.setattr("app.ext.unipile.get_settings", _half_configured)
    org = await make_org(db_session, slug="li-halfcfg")
    user = await make_user(db_session, org=org)
    # no webhook secret → refused
    assert await connections_service.start_linkedin_connect(db_session, user_id=user.id) is None


def test_linkedin_connect_is_off_without_a_webhook_secret() -> None:
    half = Settings(unipile_api_key="k", unipile_dsn=_DSN, session_cookie_password="c" * 44)
    assert half.linkedin_connect_enabled is False
    # ...and it is not a sign-in provider, so it can't stand in for one.
    assert half.auth_enabled is False
    whole = Settings(
        unipile_api_key="k",
        unipile_dsn=_DSN,
        unipile_webhook_secret="s",
        session_cookie_password="c" * 44,
    )
    assert whole.linkedin_connect_enabled is True


# --- the notify webhook boundary (HTTP) --------------------------------------


# --- email/password login (generic: verifies the user's stored hash) ---------


async def _seed_password_user(session: AsyncSession, *, slug: str) -> str:
    email = f"agent-{slug}@acme.test"
    session.add(
        User(
            email=email,
            name="Agent",
            password_hash=hash_password("testpass"),
            email_verified_at=datetime.now(UTC),
        )
    )
    await session.flush()
    return email


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
