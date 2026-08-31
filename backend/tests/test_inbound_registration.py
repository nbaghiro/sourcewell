"""Inbound webhook registration — the step that makes the receiver actually hear anything.

Unipile pushes events only to URLs that have been subscribed, and the subscription is per
deployment (and per dev tunnel), so it has to be asserted on boot. These tests pin that it is
idempotent, that it degrades toward over-registering rather than under-registering, and that it
can never take down a boot or a sign-in.
"""

from datetime import UTC, datetime

import httpx
import pytest
import respx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.types import JsonObject
from app.ext import unipile as unipile_ext
from app.models import (
    Campaign,
    Channel,
    Connection,
    ConnectionProvider,
    ConnectionStatus,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.services.outreach import receiving
from tests.factories import make_org, make_user, make_workspace

_DSN = "https://api7.unipile.com:7777"
_EXPECTED_URL = "https://api.sourcewell.dev/webhooks/unipile?token=shh"


def _configured(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        unipile_api_key="key",
        unipile_dsn=_DSN,
        unipile_webhook_secret="shh",
        api_base_url="https://api.sourcewell.dev",
    )
    monkeypatch.setattr(receiving, "get_settings", lambda: settings)
    monkeypatch.setattr(unipile_ext, "get_settings", lambda: settings)


# --- the receiver URL --------------------------------------------------------


async def test_receiver_url_is_none_without_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receiving, "get_settings", lambda: Settings())
    assert receiving.receiver_url() is None


async def test_receiver_url_carries_the_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    _configured(monkeypatch)
    assert receiving.receiver_url() == _EXPECTED_URL


async def test_unconfigured_registration_is_a_no_op(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(receiving, "get_settings", lambda: Settings())
    assert set((await receiving.ensure_inbound_webhooks()).values()) == {"skipped"}


# --- registration ------------------------------------------------------------


@respx.mock
async def test_subscribes_every_event_source(monkeypatch: pytest.MonkeyPatch) -> None:
    """LinkedIn DMs, mailbox replies, and seat credential events each need a subscription."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(200, json={"items": []}))
    created = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(201, json={}))

    results = await receiving.ensure_inbound_webhooks()

    # `account_status` is the name Unipile accepts — plain "account" is a 400, which is how
    # the seat-disconnect subscription went missing without anyone noticing.
    assert results == {
        "messaging": "registered",
        "email": "registered",
        "account_status": "registered",
    }
    subscribed = {call.request.url for call in created.calls}
    assert len(subscribed) == 1  # all three point at the one receiver
    assert created.call_count == 3


@respx.mock
async def test_existing_subscriptions_are_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    """Re-running on every boot must not pile up duplicate subscriptions."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {"request_url": _EXPECTED_URL, "source": "messaging"},
                    {"request_url": _EXPECTED_URL, "source": "account_status"},
                ]
            },
        )
    )
    created = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(201, json={}))

    results = await receiving.ensure_inbound_webhooks()

    assert results["messaging"] == "present" and results["account_status"] == "present"
    assert results["email"] == "registered"
    assert created.call_count == 1  # only the missing one


@respx.mock
async def test_a_subscription_for_another_deployment_does_not_count(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Staging's URL sitting in the list must not convince us production is subscribed."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "request_url": "https://staging.example/webhooks/unipile?token=x",
                        "source": "messaging",
                    }
                ]
            },
        )
    )
    created = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(201, json={}))

    results = await receiving.ensure_inbound_webhooks()

    assert results["messaging"] == "registered"
    assert created.call_count == 3


@respx.mock
async def test_an_unreadable_list_registers_blindly(monkeypatch: pytest.MonkeyPatch) -> None:
    """Failing open: a duplicate delivery is deduped downstream, a missing one is silence."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(500))
    created = respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(201, json={}))

    results = await receiving.ensure_inbound_webhooks()

    assert set(results.values()) == {"registered"}
    assert created.call_count == 3


@respx.mock
async def test_a_rejected_subscription_is_reported_not_raised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(200, json={"items": []}))
    respx.post(f"{_DSN}/api/v1/webhooks").mock(return_value=httpx.Response(422, json={}))

    assert set((await receiving.ensure_inbound_webhooks()).values()) == {"failed"}


@respx.mock
async def test_a_dead_provider_never_breaks_the_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """This runs inside app startup and the sign-in notify — it must not be able to break either."""
    _configured(monkeypatch)
    respx.get(f"{_DSN}/api/v1/webhooks").mock(side_effect=httpx.ConnectError("down"))
    respx.post(f"{_DSN}/api/v1/webhooks").mock(side_effect=httpx.ConnectError("down"))

    await receiving.ensure_inbound_webhooks_quietly()  # no raise


# --- the backfill sweep: what the webhook never delivered ----------------------


async def _seated_thread(
    session: AsyncSession, *, slug: str, chat_id: str, account_id: str
) -> Enrollment:
    """An enrollment we've already sent on, plus the seat the sweep will read from."""
    org = await make_org(session, slug=slug)
    ws = await make_workspace(session, org=org)
    user = await make_user(session, org=org)
    campaign = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(workspace_id=ws.id, full_name="Lee", email="lee@x.com", skills=[], tags=[])
    session.add_all([campaign, contact])
    await session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.awaiting_reply,
    )
    session.add(enr)
    await session.flush()
    session.add_all(
        [
            Message(
                workspace_id=ws.id,
                enrollment_id=enr.id,
                direction=MessageDirection.outbound,
                channel=Channel.linkedin,
                status=MessageStatus.sent,
                body="hi",
                external_id=chat_id,
                created_at=datetime.now(UTC),
            ),
            Connection(
                organization_id=org.id,
                user_id=user.id,
                provider=ConnectionProvider.linkedin,
                external_id=account_id,
                status=ConnectionStatus.ok,
            ),
        ]
    )
    await session.flush()
    return enr


class _Conn:
    """Stands in for the Unipile client — `None` means "couldn't read", not "nothing arrived"."""

    def __init__(self, items: list[JsonObject] | None) -> None:
        self._items = items

    async def list_messages(self, *, account_id: str, since: datetime) -> list[JsonObject] | None:
        return self._items


@pytest.mark.db
async def test_sweep_recovers_a_message_the_webhook_missed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lapsed subscription or an outage loses a candidate's reply permanently, because nothing
    else ever asks the provider what arrived. The sweep asks."""
    enr = await _seated_thread(
        db_session, slug="sweep-recover", chat_id="CHAT-SWEEP", account_id="ACCT-SWEEP"
    )
    item: JsonObject = {"chat_id": "CHAT-SWEEP", "text": "did you see my note?", "id": "M-LATE"}
    monkeypatch.setattr(receiving, "unipile_connection", lambda: _Conn([item]))

    result = await receiving.sweep_inbound(db_session, now=datetime.now(UTC))
    assert result["recovered"] == 1

    bodies = (
        (
            await db_session.execute(
                select(Message.body).where(
                    Message.enrollment_id == enr.id,
                    Message.direction == MessageDirection.inbound,
                )
            )
        )
        .scalars()
        .all()
    )
    assert bodies == ["did you see my note?"]


@pytest.mark.db
async def test_sweep_overlapping_a_webhook_records_nothing_twice(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It re-reads a window rather than tracking a watermark, so it *will* re-see delivered
    messages. `provider_message_id` is what makes that free."""
    await _seated_thread(db_session, slug="sweep-dupe", chat_id="CHAT-DUPE", account_id="ACCT-DUPE")
    item: JsonObject = {"chat_id": "CHAT-DUPE", "text": "hello", "id": "M-SAME"}
    monkeypatch.setattr(receiving, "unipile_connection", lambda: _Conn([item]))

    first = await receiving.sweep_inbound(db_session, now=datetime.now(UTC))
    again = await receiving.sweep_inbound(db_session, now=datetime.now(UTC))
    assert (first["recovered"], again["recovered"]) == (1, 0)


@pytest.mark.db
async def test_an_unreadable_account_is_not_reported_as_quiet(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ "Could not read" and "nothing arrived" must not look the same — a sweep that silently reads
    nothing looks exactly like proof there is nothing to recover."""
    await _seated_thread(
        db_session, slug="sweep-blind", chat_id="CHAT-BLIND", account_id="ACCT-BLIND"
    )
    monkeypatch.setattr(receiving, "unipile_connection", lambda: _Conn(None))

    assert await receiving.sweep_inbound(db_session, now=datetime.now(UTC)) == {
        "swept": 0,
        "recovered": 0,
        "unreadable": 1,
    }
