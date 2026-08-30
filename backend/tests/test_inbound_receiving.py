"""Inbound receiving: idempotent recording, and routing off the webhook's critical path.

The receiver's contract is narrow on purpose — a provider webhook *records* a reply and returns.
Classification, the state transition, and the Outreach agent all run on the worker. These tests
pin both halves, and the redelivery guard that keeps a retried webhook from answering a candidate
twice.
"""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.api import messaging as messaging_api
from app.core.config import Settings
from app.models import (
    Campaign,
    Channel,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.services.outreach import messaging as msg_service
from tests.factories import make_org, make_workspace

_SECRET = "shh"


def _with_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        messaging_api, "get_settings", lambda: Settings(unipile_webhook_secret=_SECRET)
    )


async def _thread(session: AsyncSession, *, slug: str, chat_id: str) -> Enrollment:
    """An enrollment we've already sent on, so an inbound event can map back by chat id."""
    org = await make_org(session, slug=slug)
    ws = await make_workspace(session, org=org)
    campaign = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(
        workspace_id=ws.id, full_name="Lee", email="lee@example.com", skills=[], tags=[]
    )
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
    session.add(
        Message(
            workspace_id=ws.id,
            enrollment_id=enr.id,
            direction=MessageDirection.outbound,
            channel=Channel.linkedin,
            status=MessageStatus.sent,
            body="hi",
            external_id=chat_id,
            account_id="acct-1",
        )
    )
    await session.flush()
    return enr


async def _inbound_rows(session: AsyncSession, enrollment_id: str) -> list[Message]:
    rows = await session.execute(
        select(Message)
        .where(
            Message.enrollment_id == enrollment_id,
            Message.direction == MessageDirection.inbound,
        )
        .order_by(Message.created_at)
    )
    return list(rows.scalars().all())


def _event(chat_id: str, text: str, message_id: str | None = "MSG-1") -> dict[str, object]:
    message: dict[str, object] = {"text": text}
    if message_id is not None:
        message["id"] = message_id
    return {
        "event": "message_received",
        "account_id": "acct-1",
        "chat_id": chat_id,
        "message": message,
    }


# --- idempotency -------------------------------------------------------------


@pytest.mark.db
async def test_redelivered_webhook_does_not_duplicate_the_reply(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The same provider message id twice records once — the second call is a no-op."""
    _with_secret(monkeypatch)
    enr = await _thread(db_session, slug="rx-dup", chat_id="CHAT-1")
    event = _event("CHAT-1", "Yes, interested!")

    first = await db_client.post(f"/webhooks/unipile?token={_SECRET}", json=event)
    second = await db_client.post(f"/webhooks/unipile?token={_SECRET}", json=event)

    assert first.json()["status"] == "queued"
    assert second.json()["status"] == "duplicate"
    assert len(await _inbound_rows(db_session, enr.id)) == 1


@pytest.mark.db
async def test_redelivery_cannot_run_the_router_twice(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dangerous case: a retried webhook must not route (and so must not re-reply) twice."""
    _with_secret(monkeypatch)
    enr = await _thread(db_session, slug="rx-dup2", chat_id="CHAT-2")
    event = _event("CHAT-2", "Tell me more", message_id="MSG-2")

    await db_client.post(f"/webhooks/unipile?token={_SECRET}", json=event)
    routed = await worker.run_replies_due(db_session, now=datetime.now(UTC))
    assert routed["routed"] == 1

    # The provider retries after we've already handled it.
    await db_client.post(f"/webhooks/unipile?token={_SECRET}", json=event)
    again = await worker.run_replies_due(db_session, now=datetime.now(UTC))
    assert again["routed"] == 0
    assert len(await _inbound_rows(db_session, enr.id)) == 1


@pytest.mark.db
async def test_events_without_an_id_fall_back_to_a_content_digest(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No provider message id, but a timestamp: the digest still catches the redelivery."""
    _with_secret(monkeypatch)
    enr = await _thread(db_session, slug="rx-digest", chat_id="CHAT-3")
    event = _event("CHAT-3", "same text", message_id=None)
    message = event["message"]
    assert isinstance(message, dict)
    message["timestamp"] = "2026-08-27T10:00:00Z"

    await db_client.post(f"/webhooks/unipile?token={_SECRET}", json=event)
    second = await db_client.post(f"/webhooks/unipile?token={_SECRET}", json=event)

    assert second.json()["status"] == "duplicate"
    assert len(await _inbound_rows(db_session, enr.id)) == 1


@pytest.mark.db
async def test_an_unidentifiable_event_is_recorded_rather_than_dropped(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With neither an id nor a timestamp we can't dedup — losing a real reply is the worse bug."""
    _with_secret(monkeypatch)
    enr = await _thread(db_session, slug="rx-nokey", chat_id="CHAT-4")
    event = _event("CHAT-4", "hello", message_id=None)

    await db_client.post(f"/webhooks/unipile?token={_SECRET}", json=event)
    await db_client.post(f"/webhooks/unipile?token={_SECRET}", json=event)

    assert len(await _inbound_rows(db_session, enr.id)) == 2


# --- the webhook records; the worker routes ----------------------------------


@pytest.mark.db
async def test_webhook_records_without_transitioning_the_enrollment(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing slow happens in the handler — the enrollment only moves once the worker runs."""
    _with_secret(monkeypatch)
    enr = await _thread(db_session, slug="rx-fast", chat_id="CHAT-5")

    await db_client.post(
        f"/webhooks/unipile?token={_SECRET}", json=_event("CHAT-5", "not interested, remove me")
    )
    await db_session.refresh(enr)
    assert enr.state == EnrollmentState.awaiting_reply  # untouched by the receiver
    [recorded] = await _inbound_rows(db_session, enr.id)
    assert recorded.processed_at is None  # parked for the worker
    assert recorded.external_id == "CHAT-5"  # thread mapping kept on the inbound row too

    await worker.run_replies_due(db_session, now=datetime.now(UTC))
    await db_session.refresh(enr)
    await db_session.refresh(recorded)
    assert enr.state == EnrollmentState.opted_out
    assert recorded.processed_at is not None


@pytest.mark.db
async def test_worker_gives_up_on_a_reply_that_keeps_failing(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A poison message is retried a bounded number of times, then left alone."""
    enr = await _thread(db_session, slug="rx-poison", chat_id="CHAT-6")
    message = await msg_service.record_inbound(
        db_session, enrollment=enr, text="boom", now=datetime.now(UTC)
    )
    assert message is not None

    async def explode(*_: object, **__: object) -> str:
        raise RuntimeError("router down")

    monkeypatch.setattr(worker, "handle_reply", explode)
    for _ in range(worker._MAX_ROUTE_ATTEMPTS):
        assert (await worker.run_replies_due(db_session, now=datetime.now(UTC)))["routed"] == 0

    await db_session.refresh(message)
    assert message.attempts == worker._MAX_ROUTE_ATTEMPTS
    assert message.processed_at is not None  # stopped retrying
    assert (await worker.run_replies_due(db_session, now=datetime.now(UTC)))["routed"] == 0


@pytest.mark.db
async def test_synchronous_ingest_records_and_routes_in_one_call(db_session: AsyncSession) -> None:
    """`ingest_inbound` is the seam for callers that need the intent back immediately: unlike the
    provider webhooks it classifies inline and marks the message routed on the spot."""
    enr = await _thread(db_session, slug="rx-inapp", chat_id="CHAT-7")
    result = await msg_service.ingest_inbound(
        db_session,
        from_email="",
        enrollment_id=enr.id,
        text="Yes, I'm interested — tell me more!",
        now=datetime.now(UTC),
    )
    assert result is not None
    message, intent = result
    assert intent == "interested"
    assert message.processed_at is not None
    assert enr.state == EnrollmentState.handed_off
