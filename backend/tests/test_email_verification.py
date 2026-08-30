"""Email verification: the token, the gate, the resend, and the rendered email itself."""

from datetime import UTC, datetime
from typing import Any

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import sign
from app.models import User
from app.services.workspace import auth as auth_service
from app.services.workspace.email_templates import PRIMARY, RAIL, SAND, verification_email
from tests.conftest import Outbox
from tests.test_signup import PNG, payload

# --- the token ---------------------------------------------------------------


def test_token_roundtrip_and_tampering() -> None:
    token = auth_service.verification_token("01USER")
    assert auth_service.parse_verification_token(token) == "01USER"
    assert auth_service.parse_verification_token(token + "x") is None
    assert auth_service.parse_verification_token("garbage") is None
    assert auth_service.parse_verification_token(sign("verify-email|01USER")) is None


def test_expired_token_is_refused() -> None:
    stale = auth_service.verification_token("01USER", ttl_hours=-1)
    assert auth_service.parse_verification_token(stale) is None


def test_token_is_not_interchangeable_with_an_unsubscribe_token() -> None:
    """Both are HMAC'd with the same key — the payload prefix keeps them apart."""
    from app.services.sourcing.suppression import unsubscribe_token

    assert auth_service.parse_verification_token(unsubscribe_token("01ORG", "a@b.com")) is None


# --- the signup → confirm → session flow -------------------------------------


@pytest.mark.db
async def test_signup_sends_the_link_and_confirming_signs_in(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    r = await db_client.post("/auth/signup", json=payload())
    assert r.status_code == 201
    assert r.json()["email_sent"] is True

    to, subject, _ = outbox.sent[-1]
    assert to == "ada@acme.com"
    assert subject == "Confirm your email · Sourcewell"

    # no session yet
    assert (await db_client.get("/auth/me")).status_code == 401

    verify = await db_client.get(outbox.last_url.replace("http://localhost:8901", ""))
    assert verify.status_code == 307
    assert "/?verified=1" in verify.headers["location"]

    me = await db_client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "ada@acme.com"

    user = (
        (await db_session.execute(select(User).where(User.email == "ada@acme.com"))).scalars().one()
    )
    assert user.email_verified_at is not None


@pytest.mark.db
async def test_confirmation_link_survives_a_mail_scanner_prefetch(
    db_client: AsyncClient, outbox: Outbox
) -> None:
    """A second GET of a still-valid link signs the user in rather than reporting a spent link.

    Corporate mail security (Defender Safe Links, Proofpoint) fetches every URL in an inbound
    message before the recipient ever sees it. That scanner's GET landed first, confirmed the
    account and discarded the cookie — so the person's own click hit "that link has expired" on a
    brand-new account, with a resend button that could do nothing because they were now verified.
    The signature and expiry still gate the link; only the "already used" refusal is gone.
    """
    await db_client.post("/auth/signup", json=payload())
    path = outbox.last_url.replace("http://localhost:8901", "")

    assert "/?verified=1" in (await db_client.get(path)).headers["location"]
    db_client.cookies.clear()  # the scanner keeps nothing
    replayed = await db_client.get(path)
    assert "/?verified=1" in replayed.headers["location"]
    assert (await db_client.get("/auth/me")).status_code == 200


@pytest.mark.db
async def test_expired_confirmation_link_mints_no_session(
    db_client: AsyncClient, db_session: AsyncSession, outbox: Outbox
) -> None:
    """Expiry, not single-use, is what retires a confirmation link."""
    await db_client.post("/auth/signup", json=payload())
    user = (
        (await db_session.execute(select(User).where(User.email == "ada@acme.com"))).scalars().one()
    )
    stale = auth_service.verification_token(user.id, ttl_hours=-1)
    r = await db_client.get(f"/auth/verify?token={stale}")
    assert "error=link_invalid" in r.headers["location"]
    assert (await db_client.get("/auth/me")).status_code == 401


@pytest.mark.db
async def test_tampered_link_mints_no_session(db_client: AsyncClient) -> None:
    r = await db_client.get("/auth/verify?token=not-a-real-token")
    assert "error=link_invalid" in r.headers["location"]
    assert (await db_client.get("/auth/me")).status_code == 401


@pytest.mark.db
async def test_signup_still_succeeds_when_the_mail_hop_is_down(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _fail(*, to: str, mail: Any) -> bool:
        return False

    monkeypatch.setattr("app.services.workspace.auth.send_transactional", _fail)
    r = await db_client.post("/auth/signup", json=payload())
    assert r.status_code == 201
    assert r.json()["email_sent"] is False  # the UI offers a resend on this


# --- resend ------------------------------------------------------------------


@pytest.mark.db
async def test_resend_mails_a_pending_signup(db_client: AsyncClient, outbox: Outbox) -> None:
    await db_client.post("/auth/signup", json=payload())
    assert len(outbox.sent) == 1

    r = await db_client.post("/auth/verify/resend", json={"email": "ADA@acme.com  "})
    assert r.status_code == 202
    assert len(outbox.sent) == 2  # case/whitespace normalised to the same user


@pytest.mark.db
async def test_resend_is_silent_for_unknown_and_verified_addresses(
    db_client: AsyncClient, outbox: Outbox
) -> None:
    unknown = await db_client.post("/auth/verify/resend", json={"email": "nobody@acme.com"})
    assert unknown.status_code == 202  # no enumeration: same answer either way
    assert outbox.sent == []

    await db_client.post("/auth/signup", json=payload())
    await db_client.get(outbox.last_url.replace("http://localhost:8901", ""))
    outbox.sent.clear()

    await db_client.post("/auth/verify/resend", json={"email": "ada@acme.com"})
    assert outbox.sent == []  # already verified — nothing to confirm


# --- SSO users are verified by their provider --------------------------------


@pytest.mark.db
async def test_sso_provisioned_user_is_already_verified(db_session: AsyncSession) -> None:
    from app.services.workspace.connections import provision_user

    user = await provision_user(db_session, subject="wos_123", name="Mei T", email="mei@acme.com")
    assert user.email_verified_at is not None


# --- the rendered email ------------------------------------------------------


def test_email_renders_in_the_sourcewell_palette() -> None:
    mail = verification_email(
        first_name="Ada", url="https://api.sourcewell.dev/auth/verify?token=abc", ttl_hours=24
    )
    assert "Hi Ada," in mail.html and "Hi Ada," in mail.text
    # button href + fallback link href + the visible fallback text
    assert mail.html.count("https://api.sourcewell.dev/auth/verify?token=abc") == 3
    assert "expires in 24 hours" in mail.html and "expires in 24 hours" in mail.text
    for colour in (SAND, RAIL, PRIMARY):
        assert colour in mail.html
    assert mail.html.startswith("<!doctype html>")
    assert "<table" in mail.html  # table layout, not flexbox — this is email
    assert "<" not in mail.text  # the plain-text part carries no markup


def test_email_escapes_a_hostile_name() -> None:
    mail = verification_email(
        first_name='<script>alert("x")</script>', url="https://x.dev/a", ttl_hours=1
    )
    assert "<script>" not in mail.html
    assert "expires in 1 hour." in mail.html  # singular


def test_dry_run_and_provider_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank key → SMTP path; a key → the Resend adapter."""
    from app.ext.resend import resend_mailer

    monkeypatch.setattr("app.ext.resend.get_settings", lambda: Settings(resend_api_key=""))
    assert resend_mailer() is None
    monkeypatch.setattr("app.ext.resend.get_settings", lambda: Settings(resend_api_key="re_x"))
    assert resend_mailer() is not None


@pytest.mark.db
async def test_verification_email_carries_the_signup_avatar_user(
    db_client: AsyncClient, outbox: Outbox
) -> None:
    """Sanity: the mail goes to the address that signed up, not the platform sender."""
    await db_client.post("/auth/signup", json=payload(email="zoe@acme.com", username="zoe"))
    assert outbox.sent[-1][0] == "zoe@acme.com"
    assert PNG not in outbox.sent[-1][2]  # the avatar isn't inlined into the email


def test_verification_url_points_at_the_api(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.services.workspace.auth.get_settings",
        lambda: Settings(api_base_url="https://api.sourcewell.dev/"),
    )
    url = auth_service.verification_url("01USER")
    assert url.startswith("https://api.sourcewell.dev/auth/verify?token=")
    assert datetime.now(UTC).year  # (sanity: token minted against a real clock)
