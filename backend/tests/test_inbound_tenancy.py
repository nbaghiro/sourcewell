"""Inbound receiving across tenants: whose reply is it, and whose redelivery.

Two workspaces can work the same candidate — an agency running two clients, or the same person
sourced twice. Everything here is about the receiver refusing to mix them up: the idempotency key
is scoped to a workspace, the thread lookup is scoped to the seat that received the event, and an
address that can't be scoped is dropped rather than guessed at.
"""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api import messaging as messaging_api
from app.core.config import Settings
from app.models import (
    Campaign,
    Channel,
    ConnectionProvider,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    SeatType,
    Workspace,
)
from app.services.workspace.connections import upsert_seat
from tests.factories import make_org, make_workspace, make_workspace_member

_SECRET = "shh"
_CANDIDATE = "lee@example.com"


def _with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        messaging_api, "get_settings", lambda: Settings(unipile_webhook_secret=_SECRET)
    )


async def _tenant(
    session: AsyncSession,
    *,
    slug: str,
    chat_id: str,
    account_id: str | None = None,
    channel: Channel = Channel.linkedin,
    created_at: datetime | None = None,
) -> tuple[Workspace, Enrollment]:
    """A workspace that has already sent to the same candidate, optionally from its own seat."""
    org = await make_org(session, slug=slug)
    ws = await make_workspace(session, org=org)
    if account_id:
        user = await make_workspace_member(session, org=org, workspace=ws)
        await upsert_seat(
            session,
            organization_id=org.id,
            user_id=user.id,
            provider=ConnectionProvider.linkedin,
            account_id=account_id,
            seat_type=SeatType.recruiter,
        )
    campaign = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(workspace_id=ws.id, full_name="Lee", email=_CANDIDATE, skills=[], tags=[])
    session.add_all([campaign, contact])
    await session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.awaiting_reply,
        # Set explicitly: rows written in one transaction share Postgres' now(), so without this
        # "newest enrollment" is a tie and the ordering these tests pin is arbitrary.
        created_at=created_at or datetime.now(UTC),
    )
    session.add(enr)
    await session.flush()
    session.add(
        Message(
            workspace_id=ws.id,
            enrollment_id=enr.id,
            direction=MessageDirection.outbound,
            channel=channel,
            status=MessageStatus.sent,
            body="hi",
            external_id=chat_id,
            account_id=account_id,
        )
    )
    await session.flush()
    return ws, enr


async def _inbound(session: AsyncSession, enrollment_id: str) -> list[Message]:
    rows = await session.execute(
        select(Message).where(
            Message.enrollment_id == enrollment_id,
            Message.direction == MessageDirection.inbound,
        )
    )
    return list(rows.scalars().all())


async def _post(client: AsyncClient, payload: dict[str, object]) -> dict[str, object]:
    resp = await client.post(f"/webhooks/unipile?token={_SECRET}", json=payload)
    assert resp.status_code == 200, resp.text
    body: dict[str, object] = resp.json()
    return body


@pytest.mark.db
async def test_the_same_provider_message_id_records_in_each_workspace(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Provider ids aren't unique across accounts. One workspace recording an id must not make
    another workspace's identical id look like a redelivery — that silently ate a real reply."""
    _with_secret(monkeypatch)
    _ws_a, enr_a = await _tenant(db_session, slug="tn-a", chat_id="CHAT-A", account_id="ACCT-A")
    _ws_b, enr_b = await _tenant(db_session, slug="tn-b", chat_id="CHAT-B", account_id="ACCT-B")

    for chat, account in (("CHAT-A", "ACCT-A"), ("CHAT-B", "ACCT-B")):
        body = await _post(
            db_client,
            {
                "event": "message_received",
                "account_id": account,
                "chat_id": chat,
                "message": {"id": "SHARED-ID", "text": "Yes, interested!"},
            },
        )
        assert body["status"] == "queued"

    assert len(await _inbound(db_session, enr_a.id)) == 1
    assert len(await _inbound(db_session, enr_b.id)) == 1


@pytest.mark.db
async def test_a_redelivery_within_one_workspace_is_still_dropped(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Scoping the key per workspace must not weaken the guard it exists for."""
    _with_secret(monkeypatch)
    _ws, enr = await _tenant(db_session, slug="tn-dup", chat_id="CHAT-1", account_id="ACCT-1")
    event: dict[str, object] = {
        "event": "message_received",
        "account_id": "ACCT-1",
        "chat_id": "CHAT-1",
        "message": {"id": "MSG-1", "text": "Yes, interested!"},
    }

    assert (await _post(db_client, event))["status"] == "queued"
    assert (await _post(db_client, event))["status"] == "duplicate"
    assert len(await _inbound(db_session, enr.id)) == 1


@pytest.mark.db
async def test_the_receiving_seat_decides_which_workspace_gets_the_reply(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both workspaces are working the same candidate. The seat the event arrived on is what
    separates them — not 'whichever enrollment is newest', which is whoever enrolled last."""
    _with_secret(monkeypatch)
    older = datetime.now(UTC) - timedelta(hours=1)
    _ws_a, enr_a = await _tenant(
        db_session, slug="tn-seat-a", chat_id="CHAT-A", account_id="ACCT-A", created_at=older
    )
    # B is unambiguously newer, so the old "newest enrollment wins" rule would take every reply.
    _ws_b, enr_b = await _tenant(
        db_session, slug="tn-seat-b", chat_id="CHAT-B", account_id="ACCT-B"
    )

    body = await _post(
        db_client,
        {
            "event": "message_received",
            "account_id": "ACCT-A",
            "sender": {"email": _CANDIDATE},
            "message": {"id": "MSG-SEAT", "text": "Yes, interested!"},
        },
    )
    assert body["status"] == "queued"
    assert len(await _inbound(db_session, enr_a.id)) == 1
    assert await _inbound(db_session, enr_b.id) == []


@pytest.mark.db
async def test_an_unscopeable_reply_is_dropped_rather_than_guessed(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No thread id and no seat we recognise: the address alone spans two tenants. Recording it
    would show one customer's candidate inside another's inbox, so nothing is recorded."""
    _with_secret(monkeypatch)
    _ws_a, enr_a = await _tenant(db_session, slug="tn-amb-a", chat_id="CHAT-A")
    _ws_b, enr_b = await _tenant(db_session, slug="tn-amb-b", chat_id="CHAT-B")

    body = await _post(
        db_client,
        {
            "event": "message_received",
            "account_id": "ACCT-UNKNOWN",
            "sender": {"email": _CANDIDATE},
            "message": {"id": "MSG-AMB", "text": "Yes, interested!"},
        },
    )
    assert body["status"] == "ignored"
    assert await _inbound(db_session, enr_a.id) == []
    assert await _inbound(db_session, enr_b.id) == []


@pytest.mark.db
async def test_a_single_tenant_reply_still_threads_by_address(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ambiguity guard must not break the ordinary case: one match, no seat, still recorded."""
    _with_secret(monkeypatch)
    _ws, enr = await _tenant(db_session, slug="tn-solo", chat_id="CHAT-1", channel=Channel.email)

    body = await _post(
        db_client,
        {
            "event": "message_received",
            "sender": {"email": _CANDIDATE},
            "message": {"id": "MSG-SOLO", "text": "Yes, interested!"},
        },
    )
    assert body["status"] == "queued"
    assert len(await _inbound(db_session, enr.id)) == 1


@pytest.mark.db
async def test_a_linkedin_reply_is_recorded_on_the_linkedin_channel(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The channel comes from the thread the chat id matched. Recording every reply as email put
    the wrong icon on the bubble and skewed the composer's default channel."""
    _with_secret(monkeypatch)
    _ws, enr = await _tenant(db_session, slug="tn-chan", chat_id="CHAT-1", account_id="ACCT-1")

    await _post(
        db_client,
        {
            "event": "message_received",
            "account_id": "ACCT-1",
            "chat_id": "CHAT-1",
            "message": {"id": "MSG-CHAN", "text": "Yes, interested!"},
        },
    )
    rows = await _inbound(db_session, enr.id)
    assert [m.channel for m in rows] == [Channel.linkedin]


# --- the provider echoing our own sends --------------------------------------


@pytest.mark.db
async def test_our_own_message_echoed_back_is_not_recorded_as_a_reply(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Unipile pushes every message in a chat, including the ones we sent.

    Without an author check the receiver wrote our own outreach onto the thread as the candidate's
    reply: a fabricated inbound bubble, a sequence blocked waiting on a reply that already
    "arrived", and at full autonomy an agent answering its own message.
    """
    _with_secret(monkeypatch)
    _ws, enr = await _tenant(db_session, slug="tn-echo", chat_id="CHAT-1", account_id="ACCT-1")

    body = await _post(
        db_client,
        {
            "event": "message_received",
            "account_id": "ACCT-1",
            "chat_id": "CHAT-1",
            "message": {
                "id": "MSG-ECHO",
                "text": "Came across your work — open to a quick chat?",
                "is_sender": 1,  # the seat itself sent this
            },
        },
    )
    assert body["status"] == "own_message"
    assert await _inbound(db_session, enr.id) == []


@pytest.mark.db
async def test_a_real_reply_in_the_same_chat_is_still_recorded(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The echo guard must not swallow the thing the receiver exists for."""
    _with_secret(monkeypatch)
    _ws, enr = await _tenant(db_session, slug="tn-echo2", chat_id="CHAT-1", account_id="ACCT-1")

    body = await _post(
        db_client,
        {
            "event": "message_received",
            "account_id": "ACCT-1",
            "chat_id": "CHAT-1",
            "message": {"id": "MSG-REAL", "text": "Yes, interested!", "is_sender": 0},
        },
    )
    assert body["status"] == "queued"
    assert len(await _inbound(db_session, enr.id)) == 1
