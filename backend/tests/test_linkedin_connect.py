"""Unipile connect — identity provisioning + the connection client."""

import json
import re

import httpx
import pytest
import respx
from sqlalchemy.ext.asyncio import AsyncSession

from app.ext.unipile import UnipileConnection
from app.models import ConnectionProvider
from app.services.workspace.connections import provision_from_linkedin, seat_account_id

_DSN = "https://api1.unipile.com:1234"
_LINKEDIN = ConnectionProvider.linkedin


# --- provisioning (LinkedIn sign-in) -----------------------------------------


@pytest.mark.db
async def test_provision_first_login_creates_user_and_seat(db_session: AsyncSession) -> None:
    user = await provision_from_linkedin(
        db_session,
        member_urn="urn:li:1",
        name="Tomas R",
        email="tomas@acme.com",
        account_id="acct-1",
    )
    assert user.sso_subject == "urn:li:1"
    assert user.organization_id  # org + workspace + membership provisioned
    assert await seat_account_id(db_session, user_id=user.id, provider=_LINKEDIN) == "acct-1"


@pytest.mark.db
async def test_provision_returning_user_refreshes_seat(db_session: AsyncSession) -> None:
    u1 = await provision_from_linkedin(
        db_session, member_urn="urn:li:2", name="A", email=None, account_id="acct-a"
    )
    u2 = await provision_from_linkedin(
        db_session, member_urn="urn:li:2", name="A", email=None, account_id="acct-b"
    )
    assert u1.id == u2.id  # same identity, no duplicate org/user
    assert await seat_account_id(db_session, user_id=u2.id, provider=_LINKEDIN) == "acct-b"


# --- the Unipile connection client (respx-mocked, no live API) ----------------


@respx.mock
async def test_create_link_returns_wizard_url() -> None:
    route = respx.post(f"{_DSN}/api/v1/hosted/accounts/link").mock(
        return_value=httpx.Response(200, json={"object": "HostedAuthURL", "url": "https://wizard"})
    )
    url = await UnipileConnection("key", _DSN).create_link(
        user_ref="u1", notify_url="https://n", redirect_url="https://r"
    )
    assert url == "https://wizard"
    body = json.loads(route.calls.last.request.content)
    # Unipile rejects anything but YYYY-MM-DDTHH:MM:SS.sssZ
    assert re.match(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$", body["expiresOn"])


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


@respx.mock
async def test_ensure_webhooks_registers_missing_sources() -> None:
    respx.get(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(200, json={"items": []}))
    post = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(200, json={}))
    await UnipileConnection("key", _DSN).ensure_webhooks(
        request_url="https://hook?token=t", sources=("messaging", "account")
    )
    assert post.call_count == 2  # neither source existed → both registered


@respx.mock
async def test_ensure_webhooks_skips_already_registered() -> None:
    respx.get(f"{_DSN}/api/v1/webhooks").mock(
        return_value=httpx.Response(
            200, json={"items": [{"request_url": "https://hook?token=t", "source": "messaging"}]}
        )
    )
    post = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(200, json={}))
    await UnipileConnection("key", _DSN).ensure_webhooks(
        request_url="https://hook?token=t", sources=("messaging", "account")
    )
    assert post.call_count == 1  # only the missing 'account' source is registered


@respx.mock
async def test_delete_account_calls_unipile() -> None:
    route = respx.delete(f"{_DSN}/api/v1/accounts/acct-1").mock(
        return_value=httpx.Response(200, json={})
    )
    await UnipileConnection("key", _DSN).delete_account(account_id="acct-1")
    assert route.called
