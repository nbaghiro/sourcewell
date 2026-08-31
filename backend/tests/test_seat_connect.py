"""Unipile connect — attaching a LinkedIn *sending seat* + the connection client.

LinkedIn is not a sign-in route: the wizard is only ever started by someone already signed in,
from Settings. These pin that it stays that way.
"""

import json
import re

import httpx
import pytest
import respx
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.ext.unipile import UnipileConnection
from app.models import Connection, ConnectionProvider, LoginAttempt, SeatType, User
from app.services.workspace import connections as connections_service
from app.services.workspace.connections import user_seat
from tests.factories import make_org, make_user

_DSN = "https://api1.unipile.com:1234"
_LINKEDIN = ConnectionProvider.linkedin


# --- the Unipile connection client (respx-mocked, no live API) ----------------


@respx.mock
async def test_create_link_returns_wizard_url() -> None:
    route = respx.post(f"{_DSN}/api/v1/hosted/accounts/link").mock(
        return_value=httpx.Response(200, json={"object": "HostedAuthURL", "url": "https://wizard"})
    )
    url = await UnipileConnection("key", _DSN).create_link(
        user_ref="u1", notify_url="https://n", redirect_url="https://r", providers=["GOOGLE"]
    )
    assert url == "https://wizard"
    body = json.loads(route.calls.last.request.content)
    # Unipile rejects anything but YYYY-MM-DDTHH:MM:SS.sssZ
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", body["expiresOn"])
    # The wizard is pinned to the one provider asked for, so what comes back is what the attempt
    # recorded — that is what lets the notify hop trust it.
    assert body["providers"] == ["GOOGLE"]


@respx.mock
async def test_profile_reads_member_urn() -> None:
    respx.get(f"{_DSN}/api/v1/users/me").mock(
        return_value=httpx.Response(200, json={"member_urn": "12345", "first_name": "Tomas"})
    )
    prof = await UnipileConnection("key", _DSN).profile(account_id="acct-1")
    assert prof is not None and prof.get("member_urn") == "12345"


@respx.mock
async def test_register_webhooks_posts() -> None:
    route = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(200, json={}))
    await UnipileConnection("key", _DSN).register_webhooks(
        request_url="https://hook", source="messaging"
    )
    assert route.called


# --- Connecting a sending seat from Settings ----------------------------------


@pytest.mark.db
async def test_seat_connect_binds_the_account_to_the_signed_in_user(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A connect attempt (user_id pre-set) attaches the seat instead of provisioning a new user."""
    org = await make_org(db_session, slug="seat-connect")
    user = await make_user(db_session, org=org, email="rec@example.com")
    db_session.add(LoginAttempt(state="ST-1", status="pending", user_id=user.id))
    await db_session.flush()
    users_before = len((await db_session.execute(select(User))).scalars().all())

    monkeypatch.setattr(connections_service, "unipile_connection", lambda: None)
    await connections_service.complete_seat_connect(
        db_session, state="ST-1", account_id="ACCT-SEAT"
    )

    seat = await user_seat(db_session, user_id=user.id, provider=_LINKEDIN)
    assert seat is not None and seat.external_id == "ACCT-SEAT"
    users_after = len((await db_session.execute(select(User))).scalars().all())
    assert users_after == users_before  # nobody was provisioned


@pytest.mark.db
async def test_notify_ignores_an_attempt_that_names_no_user(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """LinkedIn can no longer create an account.

    The notify hop used to provision a whole org off a `member_urn` whenever the attempt carried
    no user — that was the sign-in half. With sign-in gone, an attempt with no user is either a
    stale row or a forged state, and either way must not mint anyone.
    """
    db_session.add(LoginAttempt(state="ST-ORPHAN", status="pending"))
    await db_session.flush()
    users_before = len((await db_session.execute(select(User))).scalars().all())

    monkeypatch.setattr(connections_service, "unipile_connection", lambda: None)
    await connections_service.complete_seat_connect(
        db_session, state="ST-ORPHAN", account_id="ACCT-X"
    )

    assert len((await db_session.execute(select(User))).scalars().all()) == users_before
    attempt = (
        await db_session.execute(select(LoginAttempt).where(LoginAttempt.state == "ST-ORPHAN"))
    ).scalar_one()
    assert attempt.status == "pending"  # untouched


@pytest.mark.db
async def test_notify_endpoint_is_token_gated(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notify hop is public — it is what binds a Unipile account to one of our users, so an
    unauthenticated caller who could forge it would attach their own LinkedIn seat to someone
    else's account. It must reject anything without the shared secret."""
    monkeypatch.setattr(
        "app.api.settings.get_settings", lambda: Settings(unipile_webhook_secret="s3cret")
    )
    body = {"account_id": "acct-1", "name": "some-state"}

    assert (await db_client.post("/settings/connections/notify", json=body)).status_code == 401
    assert (
        await db_client.post("/settings/connections/notify?token=wrong", json=body)
    ).status_code == 401
    assert (
        await db_client.post("/settings/connections/notify?token=s3cret", json=body)
    ).status_code == 200  # accepted; unknown state is a no-op


@pytest.mark.db
async def test_notify_is_disabled_when_no_secret_is_configured(db_client: AsyncClient) -> None:
    """A blank secret must not mean "any token passes"."""
    r = await db_client.post(
        "/settings/connections/notify?token=", json={"account_id": "a", "name": "b"}
    )
    assert r.status_code == 401


@pytest.mark.db
async def test_an_email_wizard_writes_an_email_seat(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The notify payload carries only an account id, so the provider has to come off the attempt.

    Reading it from anywhere else would write a Gmail mailbox as a LinkedIn profile — and the send
    path resolves a seat *by provider*, so that seat would then be picked to carry LinkedIn
    messages it cannot send.
    """
    org = await make_org(db_session, slug="seat-email")
    user = await make_user(db_session, org=org, email="rec@example.com")
    db_session.add(
        LoginAttempt(
            state="ST-MAIL", status="pending", user_id=user.id, provider=ConnectionProvider.gmail
        )
    )
    await db_session.flush()

    monkeypatch.setattr(connections_service, "unipile_connection", lambda: None)
    await connections_service.complete_seat_connect(
        db_session, state="ST-MAIL", account_id="ACCT-GMAIL"
    )

    seat = (
        (await db_session.execute(select(Connection).where(Connection.user_id == user.id)))
        .scalars()
        .one()
    )
    assert seat.provider is ConnectionProvider.gmail
    assert seat.external_id == "ACCT-GMAIL"
    # A mailbox has no LinkedIn tier to read; it can send, or it isn't connected.
    assert seat.seat_type is SeatType.email


# --- seat tier ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("profile", "expected"),
    [
        # A free account: every capability flag absent or false.
        ({"premium": False, "recruiter": None, "sales_navigator": None}, SeatType.basic),
        ({"premium": True, "recruiter": None, "sales_navigator": None}, SeatType.premium),
        ({"premium": True, "sales_navigator": True}, SeatType.sales_nav),
        ({"premium": True, "recruiter": True, "sales_navigator": True}, SeatType.recruiter),
        # The paid tiers can come back as an object describing the subscription, not just a bool.
        ({"recruiter": {"seat": "full"}}, SeatType.recruiter),
        # Unreachable / unparseable profile: claim the least, never a capability we can't see.
        (None, SeatType.basic),
        ({}, SeatType.basic),
    ],
)
def test_seat_tier_is_read_from_the_profile_not_assumed(
    profile: object, expected: SeatType
) -> None:
    """It was hardcoded to `recruiter`, so every free account was labelled a Recruiter seat — and
    the tier is what decides whether InMail is possible at all."""
    assert connections_service.seat_type_from_profile(profile) == expected
