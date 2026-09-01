"""Email/password sign-in: normalisation, throttling, account state, and the reset flow."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import hash_password, verify_password
from app.models import AuditEvent, MembershipRole, User, UserStatus
from app.services.workspace import auth as auth_service
from tests.conftest import Outbox
from tests.factories import make_membership, make_org, make_user

PASSWORD = "correct-horse"


async def make_login_user(
    session: AsyncSession,
    *,
    slug: str,
    email: str = "ada@acme.com",
    verified: bool = True,
    password: str | None = PASSWORD,
    status: UserStatus = UserStatus.active,
) -> User:
    org = await make_org(session, slug=slug)
    user = User(
        email=email,
        name="Ada Lovelace",
        first_name="Ada",
        status=status,
        password_hash=hash_password(password) if password else None,
        email_verified_at=datetime.now(UTC) if verified else None,
    )
    session.add(user)
    await session.flush()
    await make_membership(session, user=user, org=org, role=MembershipRole.org_admin)
    return user


async def login(client: AsyncClient, email: str, password: str = PASSWORD) -> Any:
    return await client.post("/auth/password", json={"email": email, "password": password})


# --- the happy path + normalisation ------------------------------------------


@pytest.mark.db
async def test_login_sets_a_session(db_client: AsyncClient, db_session: AsyncSession) -> None:
    await make_login_user(db_session, slug="li-ok")
    r = await login(db_client, "ada@acme.com")
    assert r.status_code == 200
    assert (await db_client.get("/auth/me")).json()["user"]["email"] == "ada@acme.com"


@pytest.mark.parametrize(
    "typed",
    ["ada@acme.com", "Ada@Acme.com", "ADA@ACME.COM", "  ada@acme.com  "],
)
@pytest.mark.db
async def test_login_normalises_the_typed_address(
    db_client: AsyncClient, db_session: AsyncSession, typed: str
) -> None:
    """Signup stores lowercase; a capitalised or padded address must still sign in."""
    await make_login_user(db_session, slug="li-norm")
    assert (await login(db_client, typed)).status_code == 200


@pytest.mark.db
async def test_wrong_password_is_refused(db_client: AsyncClient, db_session: AsyncSession) -> None:
    await make_login_user(db_session, slug="li-wrong")
    r = await login(db_client, "ada@acme.com", "nope")
    assert r.status_code == 401
    assert (await db_client.get("/auth/me")).status_code == 401


@pytest.mark.db
async def test_unknown_address_still_costs_a_hash(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No account → we still verify against a dummy hash, so timing doesn't enumerate users."""
    calls: list[str] = []

    def _counting(password: str, stored: str) -> bool:
        calls.append(stored)
        return verify_password(password, stored)

    monkeypatch.setattr("app.services.workspace.auth.verify_password", _counting)
    assert (await login(db_client, "nobody@acme.com")).status_code == 401
    assert len(calls) == 1


# --- throttling ---------------------------------------------------------------


@pytest.mark.db
async def test_repeated_failures_lock_the_account(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.workspace.auth.get_settings",
        lambda: Settings(login_max_attempts=3, login_lockout_minutes=15),
    )
    user = await make_login_user(db_session, slug="li-lock")

    for _ in range(3):
        assert (await login(db_client, "ada@acme.com", "nope")).status_code == 401

    locked = await login(db_client, "ada@acme.com", PASSWORD)  # the *right* password now
    assert locked.status_code == 429
    assert locked.json()["detail"] == "too_many_attempts"
    assert 0 < int(locked.headers["retry-after"]) <= 15 * 60
    assert user.locked_until is not None


@pytest.mark.db
async def test_lock_expires(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "app.services.workspace.auth.get_settings",
        lambda: Settings(login_max_attempts=1, login_lockout_minutes=15),
    )
    user = await make_login_user(db_session, slug="li-unlock")
    assert (await login(db_client, "ada@acme.com", "nope")).status_code == 401
    assert (await login(db_client, "ada@acme.com")).status_code == 429

    user.locked_until = datetime.now(UTC) - timedelta(seconds=1)  # window elapsed
    await db_session.flush()
    assert (await login(db_client, "ada@acme.com")).status_code == 200


@pytest.mark.db
async def test_success_clears_the_failure_count(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, slug="li-reset-count")
    await login(db_client, "ada@acme.com", "nope")
    assert user.failed_login_count == 1
    assert (await login(db_client, "ada@acme.com")).status_code == 200
    assert user.failed_login_count == 0


@pytest.mark.db
async def test_a_failed_attempt_is_committed_not_rolled_back(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 401 that answers a bad password rolls the request back (`get_session` does that on any
    raise), so the counter has to be committed as it's recorded — otherwise no lock ever sticks."""
    await make_login_user(db_session, slug="li-commit")
    commits: list[int] = []
    monkeypatch.setattr(
        db_session,
        "commit",
        lambda: commits.append(1) or _noop(),  # type: ignore[func-returns-value]
    )

    await auth_service.password_login(db_session, email="ada@acme.com", password="nope")
    assert commits, "a failed attempt must be persisted independently of the request transaction"


async def _noop() -> None:
    return None


# --- account state ------------------------------------------------------------


@pytest.mark.db
async def test_disabled_account_cannot_sign_in(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_user(db_session, slug="li-disabled", status=UserStatus.disabled)
    r = await login(db_client, "ada@acme.com")
    assert r.status_code == 403
    assert r.json()["detail"] == "account_disabled"
    assert (await db_client.get("/auth/me")).status_code == 401


@pytest.mark.db
async def test_unverified_account_is_told_to_confirm(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_user(db_session, slug="li-unverified", verified=False)
    r = await login(db_client, "ada@acme.com")
    assert r.status_code == 403
    assert r.json()["detail"] == "email_not_verified"


@pytest.mark.db
async def test_sso_only_account_has_no_password(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    await make_login_user(db_session, slug="li-sso", password=None)
    assert (await login(db_client, "ada@acme.com", "anything")).status_code == 401


@pytest.mark.db
async def test_login_is_audited(db_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session, slug="li-audit")
    await login(db_client, "ada@acme.com")
    events = (
        (await db_session.execute(select(AuditEvent).where(AuditEvent.action == "auth.login")))
        .scalars()
        .all()
    )
    assert [e.actor_user_id for e in events] == [user.id]


# --- password reset -----------------------------------------------------------


@pytest.mark.db
async def test_forgot_password_mails_a_working_link(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    await make_login_user(db_session, slug="pw-forgot")
    r = await db_client.post("/auth/password/forgot", json={"email": "ADA@acme.com "})
    assert r.status_code == 202
    to, subject, html = outbox.sent[-1]
    assert (to, subject) == ("ada@acme.com", "Reset your Sourcewell password")

    token = html.split("/reset-password?token=")[1].split('"')[0]
    reset = await db_client.post(
        "/auth/password/reset", json={"token": token, "password": "brand-new-secret"}
    )
    assert reset.status_code == 200
    assert (await db_client.get("/auth/me")).json()["user"]["email"] == "ada@acme.com"

    db_client.cookies.clear()
    assert (await login(db_client, "ada@acme.com", "brand-new-secret")).status_code == 200
    assert (await login(db_client, "ada@acme.com", PASSWORD)).status_code == 401  # old one dead


@pytest.mark.db
async def test_reset_link_is_single_use(db_client: AsyncClient, db_session: AsyncSession) -> None:
    user = await make_login_user(db_session, slug="pw-once")
    token = auth_service.password_reset_token(user)
    body = {"token": token, "password": "brand-new-secret"}
    assert (await db_client.post("/auth/password/reset", json=body)).status_code == 200
    replay = await db_client.post("/auth/password/reset", json=body)
    assert replay.status_code == 400
    assert replay.json()["detail"] == "reset_link_invalid"


@pytest.mark.db
async def test_reset_link_expires_and_resists_tampering(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, slug="pw-expiry")
    stale = auth_service.password_reset_token(user, ttl_minutes=-1)
    assert (
        await db_client.post("/auth/password/reset", json={"token": stale, "password": "abcdefgh"})
    ).status_code == 400
    assert (
        await db_client.post(
            "/auth/password/reset", json={"token": "forged", "password": "abcdefgh"}
        )
    ).status_code == 400


@pytest.mark.db
async def test_reset_enforces_the_password_floor(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, slug="pw-floor")
    r = await db_client.post(
        "/auth/password/reset",
        json={"token": auth_service.password_reset_token(user), "password": "short"},
    )
    assert r.status_code == 422


@pytest.mark.db
async def test_reset_clears_a_lockout_and_confirms_the_address(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    user = await make_login_user(db_session, slug="pw-unlock", verified=False)
    user.locked_until = datetime.now(UTC) + timedelta(minutes=15)
    await db_session.flush()

    r = await db_client.post(
        "/auth/password/reset",
        json={"token": auth_service.password_reset_token(user), "password": "brand-new-secret"},
    )
    assert r.status_code == 200
    assert user.locked_until is None
    assert user.email_verified_at is not None  # clicking the link proved they own the address


@pytest.mark.db
async def test_forgot_password_is_silent_for_accounts_it_cannot_serve(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    """Unknown addresses, disabled accounts — and, critically, an account whose address nobody has
    proven.

    That last one is an unaccepted invite: a `User` row an admin created carrying someone else's
    address. Mailing it a "set your password" link would hand the real owner of that mailbox an
    account inside a stranger's org — the same capture the invitation link exists to prevent.
    """
    await make_login_user(
        db_session, slug="pw-off", email="off@acme.com", status=UserStatus.disabled
    )
    await make_login_user(
        db_session,
        slug="pw-unproven",
        email="unproven@acme.com",
        password=None,
        verified=False,  # invited, never accepted
    )
    for address in ("nobody@acme.com", "off@acme.com", "unproven@acme.com"):
        assert (
            await db_client.post("/auth/password/forgot", json={"email": address})
        ).status_code == 202
    assert outbox.sent == []


@pytest.mark.db
async def test_a_proven_account_with_no_password_is_sent_one_to_set(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    """An invited teammate who accepted, or someone who has only ever used Google.

    Without this they had one way in ever — the invitation link — and nothing at all once its
    session expired, because "forgot password" refused any account that had no password to reset.
    """
    user = await make_login_user(
        db_session, slug="pw-first", email="first@acme.com", password=None, verified=True
    )
    assert (
        await db_client.post("/auth/password/forgot", json={"email": "first@acme.com"})
    ).status_code == 202

    to, subject, _html = outbox.sent[-1]
    assert to == "first@acme.com"
    assert subject == "Set your Sourcewell password"  # not "reset" — they never had one

    token = auth_service.password_reset_token(user)
    r = await db_client.post(
        "/auth/password/reset", json={"token": token, "password": "a-first-password"}
    )
    assert r.status_code == 200
    assert (await login(db_client, "first@acme.com", "a-first-password")).status_code == 200


@pytest.mark.db
async def test_reset_revokes_sessions_minted_with_the_old_password(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The classic hole: an attacker holding a stolen session cookie keeps it after the victim
    resets their password. The sealed cookie carries a session epoch, and the reset bumps it."""
    user = await make_login_user(db_session, slug="pw-revoke")
    stolen = auth_service.mint_session_for(user)
    assert await auth_service._session_user_id(db_session, stolen) == user.id

    await db_client.post(
        "/auth/password/reset",
        json={"token": auth_service.password_reset_token(user), "password": "brand-new-secret"},
    )
    assert await auth_service._session_user_id(db_session, stolen) is None
    # ...while the cookie the reset itself issued still works
    assert await auth_service._session_user_id(db_session, auth_service.mint_session_for(user))


@pytest.mark.db
async def test_a_cookie_without_an_epoch_is_refused(db_session: AsyncSession) -> None:
    """The pre-epoch cookie format carried a bare user id — no longer accepted."""
    from app.core.crypto import seal

    user = await make_login_user(db_session, slug="pw-legacy")
    assert await auth_service._session_user_id(db_session, seal(user.id)) is None


def test_reset_email_renders_in_the_palette() -> None:
    from app.services.workspace.email_templates import PRIMARY, password_reset_email

    mail = password_reset_email(first_name="Ada", url="https://app.dev/r?token=x", ttl_minutes=60)
    assert "Reset your password" in mail.html
    assert "expires in 1 hour" in mail.html and "expires in 1 hour" in mail.text
    assert PRIMARY in mail.html
    assert "<" not in mail.text


# --- a corrupted stored hash fails the check, it doesn't crash the request ------


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "notahash",
        "scrypt$onlytwo",
        "bcrypt$aabb$ccdd",
        "scrypt$nothex$aabb",  # a salt that isn't hex — `bytes.fromhex` raised here
        "scrypt$aab$ccdd",  # odd-length hex, same
        "scrypt$$",
    ],
)
def test_a_malformed_stored_hash_is_a_failed_check_not_an_exception(stored: str) -> None:
    """One corrupted row must not 500 every sign-in attempt against it.

    "Wrong password" is both true and actionable — the user can reset it — where a 500 is neither.
    Unparseable stored material can't authenticate anyone either way.
    """
    assert verify_password("anything", stored) is False


@pytest.mark.db
async def test_sign_in_against_a_corrupted_hash_is_rejected_cleanly(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    org = await make_org(db_session, slug="pw-corrupt")
    user = await make_user(db_session, org=org, email="corrupt@acme.com")
    user.password_hash = "scrypt$nothex$deadbeef"  # e.g. a truncated column, a bad restore
    await db_session.flush()

    r = await db_client.post(
        "/auth/password", json={"email": "corrupt@acme.com", "password": "whatever"}
    )
    assert r.status_code == 401


@pytest.mark.db
async def test_a_correct_password_clears_its_strikes_even_when_the_account_is_unverified(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The strikes were earned by *wrong* passwords. The unverified and disabled exits raise out
    of the handler and `get_session` rolls a raising request back, so a reset placed after those
    checks was thrown away — an unverified account carried its strikes across every correct
    sign-in and crept toward a lockout it had not earned."""
    user = await make_user(
        db_session,
        org=await make_org(db_session, slug="strikes-unverified"),
        email="unverified@acme.com",
    )
    user.password_hash = hash_password("correct-horse")
    user.email_verified_at = None
    user.failed_login_count = 7
    await db_session.commit()

    r = await db_client.post(
        "/auth/password", json={"email": "unverified@acme.com", "password": "correct-horse"}
    )
    assert r.status_code == 403
    assert "email_not_verified" in r.text

    await db_session.refresh(user)
    assert user.failed_login_count == 0
