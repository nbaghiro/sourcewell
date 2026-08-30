"""Security properties of the auth surface: what must stay true for it to be safe to deploy."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import limits
from app.core.config import Settings
from app.core.crypto import seal, unseal
from app.main import create_app
from app.models import UserStatus
from app.services.workspace import auth as auth_service
from app.services.workspace import connections as connections_service
from app.services.workspace.connections import provision_user
from tests.factories import make_org, make_user
from tests.test_signup import payload

_KEY = "8Q0hFTMWShy6mJnPz5A4mUXsPJIHVaFvpsRwqxhLZTk="  # a throwaway Fernet key


def prod(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "environment": "production",
        "session_cookie_password": _KEY,
        "signing_secret": "s" * 32,
        "cookie_secure": True,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- X-User-Id must never be trusted outside local ----------------------------


def test_header_auth_is_local_only() -> None:
    """`X-User-Id` names whoever the caller says they are. A password-only production
    deployment has no SSO provider configured either — gating on that would have handed every
    account to anyone who could set a header."""
    assert Settings(environment="local").header_auth_enabled is True
    assert prod().header_auth_enabled is False
    assert prod(workos_api_key="", workos_client_id="").header_auth_enabled is False
    assert Settings(environment="staging").header_auth_enabled is False


@pytest.mark.db
async def test_header_impersonation_is_refused_in_production(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org = await make_org(db_session, slug="hdr-prod")
    victim = await make_user(db_session, org=org)
    monkeypatch.setattr("app.services.workspace.auth.get_settings", prod)

    app = create_app()

    async def _override() -> AsyncSession:  # pragma: no cover - trivial
        return db_session

    from app.core.db import get_session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        r = await client.get("/auth/me", headers={"X-User-Id": victim.id})
    assert r.status_code == 401


# --- the app refuses to boot with development secrets -------------------------


def test_production_config_errors_catch_every_silent_footgun() -> None:
    assert Settings(environment="local").production_config_errors() == []
    assert prod().production_config_errors() == []

    missing_key = " ".join(prod(session_cookie_password="").production_config_errors())
    assert "SESSION_COOKIE_PASSWORD" in missing_key

    forgeable = " ".join(
        prod(session_cookie_password="", signing_secret="").production_config_errors()
    )
    assert "SIGNING_SECRET" in forgeable  # links would be signed with the public dev fallback

    assert "COOKIE_SECURE" in " ".join(prod(cookie_secure=False).production_config_errors())
    assert "COOKIE_SECURE" in " ".join(
        prod(cookie_secure=False, cookie_samesite="none").production_config_errors()
    )


def test_app_refuses_to_start_insecure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.main.get_settings", lambda: prod(session_cookie_password=""))
    with pytest.raises(RuntimeError, match="Refusing to start"):
        create_app()


# --- sessions ------------------------------------------------------------------


def test_session_tokens_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    """The cookie's max-age is a client-side hint; a copied value must die server-side too."""
    monkeypatch.setattr("app.core.crypto.get_settings", lambda: prod())
    sealed = seal("01USER")
    assert unseal(sealed, ttl_seconds=60) == "01USER"
    with pytest.raises(RuntimeError, match="cannot decrypt"):
        unseal(sealed, ttl_seconds=-1)  # older than the window


def test_sealed_secrets_do_not_expire(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stored provider keys use the same sealing and must stay readable until rotated."""
    monkeypatch.setattr("app.core.crypto.get_settings", lambda: prod())
    assert unseal(seal("provider-api-key")) == "provider-api-key"


# --- link tokens are not interchangeable ---------------------------------------


def test_link_tokens_are_typed(db_session: AsyncSession) -> None:
    """Verification, reset and unsubscribe links share one signing key — the type prefix is what
    stops a token minted for one purpose being replayed as another."""
    from app.services.sourcing.suppression import unsubscribe_token

    verification = auth_service.verification_token("01USER")
    assert auth_service.parse_verification_token(verification) == "01USER"
    assert auth_service._read_link_token(verification, "password-reset", field_count=2) is None
    assert auth_service.parse_verification_token(unsubscribe_token("01ORG", "a@b.com")) is None


# --- avatars -------------------------------------------------------------------


@pytest.mark.parametrize(
    "avatar",
    [
        "data:image/svg+xml;base64,PHN2Zz48c2NyaXB0PmFsZXJ0KDEpPC9zY3JpcHQ+PC9zdmc+",
        "data:text/html;base64,PHNjcmlwdD5hbGVydCgxKTwvc2NyaXB0Pg==",
        "data:image/svg+xml,<svg onload=alert(1)>",
        "javascript:alert(1)",
    ],
)
@pytest.mark.db
async def test_signup_rejects_script_bearing_avatars(db_client: AsyncClient, avatar: str) -> None:
    """The avatar is echoed to everyone else in the org, so only raster images are accepted."""
    r = await db_client.post("/auth/signup", json=payload(avatar=avatar))
    assert r.status_code == 422


# --- abuse limits ---------------------------------------------------------------


@pytest.fixture
def limited(monkeypatch: pytest.MonkeyPatch) -> None:
    """Turn the limiter on (the suite runs with it off) and start from clean counters."""
    limits.reset()
    monkeypatch.setattr(
        limits,
        "get_settings",
        lambda: Settings(
            auth_rate_limits_enabled=True,
            auth_signup_per_hour=2,
            auth_mail_per_hour=3,
            auth_email_cooldown_seconds=60,
        ),
    )


@pytest.mark.db
async def test_signup_is_throttled_per_ip(db_client: AsyncClient, limited: None) -> None:
    """Signup creates an organization and stores an uploaded image, unauthenticated."""
    for i in range(2):
        r = await db_client.post(
            "/auth/signup", json=payload(email=f"a{i}@acme.com", username=f"ada{i}")
        )
        assert r.status_code == 201
    blocked = await db_client.post(
        "/auth/signup", json=payload(email="a9@acme.com", username="ada9")
    )
    assert blocked.status_code == 429
    assert int(blocked.headers["retry-after"]) > 0


@pytest.mark.db
async def test_mail_endpoints_cannot_be_used_to_bomb_an_address(
    db_client: AsyncClient, limited: None
) -> None:
    """`forgot` and `resend` send mail to an address the caller need not own — one message per
    address per cooldown, no matter who asks."""
    first = await db_client.post("/auth/password/forgot", json={"email": "victim@acme.com"})
    assert first.status_code == 202
    again = await db_client.post("/auth/password/forgot", json={"email": "victim@acme.com"})
    assert again.status_code == 429
    # a different address is unaffected
    assert (
        await db_client.post("/auth/password/forgot", json={"email": "other@acme.com"})
    ).status_code == 202


@pytest.mark.db
async def test_address_cooldown_ignores_case_and_padding(
    db_client: AsyncClient, limited: None
) -> None:
    assert (
        await db_client.post("/auth/verify/resend", json={"email": "victim@acme.com"})
    ).status_code == 202
    assert (
        await db_client.post("/auth/verify/resend", json={"email": "  VICTIM@Acme.com "})
    ).status_code == 429


def test_limiter_window_rolls_over() -> None:
    limits.reset()
    assert limits._consume("s", "k", limit=1, window_s=0.01) == 0
    assert limits._consume("s", "k", limit=1, window_s=0.01) > 0
    import time

    time.sleep(0.02)
    assert limits._consume("s", "k", limit=1, window_s=0.01) == 0


def test_limiter_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A spray of unique keys must not grow the bucket map without limit."""
    limits.reset()
    monkeypatch.setattr(limits, "_MAX_BUCKETS", 50)
    for i in range(500):
        limits._consume("spray", f"key-{i}", limit=1, window_s=60)
    assert len(limits._BUCKETS) <= 50


# --- connect attempts don't accumulate ------------------------------------------


@pytest.mark.db
async def test_stale_login_attempts_are_purged(db_session: AsyncSession) -> None:
    from sqlalchemy import select

    from app.models import LoginAttempt

    old = LoginAttempt(state="ancient", status="pending")
    db_session.add(old)
    await db_session.flush()
    old.created_at = datetime.now(UTC) - timedelta(days=3)
    await db_session.flush()

    await connections_service._purge_stale_attempts(db_session)
    remaining = (
        (await db_session.execute(select(LoginAttempt).where(LoginAttempt.state == "ancient")))
        .scalars()
        .all()
    )
    assert remaining == []


# --- a disabled account stays out, whichever door it uses ------------------------


@pytest.mark.db
async def test_sso_callback_refuses_a_disabled_account(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Disabling a user must survive an SSO round-trip.

    `password_login` refuses a disabled account, but the SSO callback minted a session for anyone
    provisioning resolved — and provisioning happily returns an existing user (and re-activates a
    matched-by-email one). So an admin's revocation was undone by clicking "Continue with Google".
    """
    org = await make_org(db_session, slug="hard-disabled")
    user = await make_user(db_session, org=org, email="gone@acme.com")
    user.sso_subject = "workos-user-gone"
    user.status = UserStatus.disabled
    await db_session.flush()

    async def _complete(*_args: object, **_kwargs: object) -> str:
        return user.id

    monkeypatch.setattr(auth_service, "complete_workos_login", _complete)
    r = await db_client.get("/auth/callback?code=any")
    assert "error=account_disabled" in r.headers["location"]
    assert (await db_client.get("/auth/me")).status_code == 401


@pytest.mark.db
async def test_sso_does_not_reactivate_a_disabled_user_by_email(db_session: AsyncSession) -> None:
    """The link-by-email path must not re-activate a disabled account — matching one used to clear
    its `disabled` status and hand the caller a working session.

    The row is returned untouched rather than forked: an address is global and unique, so there is
    no second account to provision. `/auth/callback` is what refuses to mint it a session.
    """
    org = await make_org(db_session, slug="hard-relink")
    disabled = await make_user(db_session, org=org, email="revoked@acme.com")
    disabled.status = UserStatus.disabled
    await db_session.flush()

    provisioned = await provision_user(
        db_session, subject="workos-new", name="Revoked", email="revoked@acme.com"
    )
    assert provisioned.id == disabled.id
    await db_session.refresh(disabled)
    assert disabled.status is UserStatus.disabled
    assert disabled.sso_subject is None
