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
from app.services.outreach import receiving
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


# --- the shapes Unipile actually sends -----------------------------------------

# Verbatim from a live GOOGLE_OAUTH account: an email reply names its sender in `from_attendee`,
# carries the text in `body_plain` (`body` is the HTML part), and has no `sender`/`from` key at
# all. We only looked for the chat shape, so every email reply resolved to nobody and was dropped
# — with a 200 back to the provider, so it looked delivered from both ends.
_REAL_EMAIL_EVENT: dict[str, object] = {
    "object": "Email",
    "kind": "2_full",
    "type": "GOOGLE_OAUTH",
    "account_id": "ACCT-MAIL",
    "date": "2026-08-31T13:22:11.000Z",
    "from_attendee": {
        "display_name": "Lee Park",
        "identifier": "lee@example.com",
        "identifier_type": "EMAIL_ADDRESS",
    },
    "subject": "Re:",
    "body": "<div>Lets try it out</div>",
    "body_plain": "Lets try it out",
    "message_id": "<2CE29E53-2E68@icloud.com>",
    "thread_id": "THREAD-9",
}


@pytest.mark.db
async def test_a_real_email_reply_is_matched_by_its_sender(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _with_secret(monkeypatch)
    enr = await _thread(db_session, slug="rx-real-email", chat_id="SOMETHING-ELSE")

    r = await db_client.post(f"/webhooks/unipile?token={_SECRET}", json=_REAL_EMAIL_EVENT)
    assert r.json()["status"] == "queued", r.text

    rows = await _inbound_rows(db_session, enr.id)
    # ...and the plain part, not the HTML: markup on the thread also feeds tags to the classifier.
    assert [m.body for m in rows] == ["Lets try it out"]


@pytest.mark.db
async def test_an_unplaceable_event_is_reported_not_swallowed(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A dropped reply used to be entirely silent: the provider got its 200 and the recruiter
    never learned the candidate had written back."""
    _with_secret(monkeypatch)
    with caplog.at_level("WARNING"):
        r = await db_client.post(
            f"/webhooks/unipile?token={_SECRET}",
            json={"body_plain": "hello?", "from_attendee": {"identifier": "nobody@nowhere.io"}},
        )
    assert r.json()["status"] == "ignored"
    assert any("dropping an event" in rec.getMessage() for rec in caplog.records)


# --- a reply is what they wrote, not the whole conversation quoted back ---------


@pytest.mark.parametrize(
    ("client", "raw", "expected"),
    [
        (
            # Verbatim from a live reply, Cyrillic and all: the attribution is localised, which is
            # why the stripper keys off the quote marker rather than matching "wrote:" in English.
            "apple mail",
            "Lets try it out\r\n\r\n> 31 авг. 2026 г., в 15:14, "  # noqa: RUF001
            "rauljan7@gmail.com написал(а):\r\n>\r\n> anything else there?",  # noqa: RUF001
            "Lets try it out",
        ),
        (
            "gmail",  # attribution sits above the quote, unquoted
            "Sounds good, let's talk Thursday.\n\n"
            "On Mon, Aug 31, 2026 at 3:14 PM Raul <rauljan7@gmail.com> wrote:\n"
            "> anything else there?\n> --\n> Unsubscribe: https://x/y",
            "Sounds good, let's talk Thursday.",
        ),
        (
            "outlook",
            "Yes please.\n\n-----Original Message-----\nFrom: Raul\nSent: Monday\n",
            "Yes please.",
        ),
        (
            # Verbatim from a live Gmail reply. Two things broke here: the attribution is
            # hard-wrapped, so the address is on one line and "wrote:" on the next, and the quoted
            # body never arrived — leaving the header with no ">" block under it to key off. The
            # sender's own address was left dangling on the thread.
            "gmail, wrapped attribution and no quoted body",
            "Yes, let's do it right now!\n\n"
            "On Wed, Sep 2, 2026 at 1:51 PM rauljan7@gmail.com <rauljan7@gmail.com>\n"
            "wrote:.",
            "Yes, let's do it right now!",
        ),
        (
            "apple mail, attribution with nothing quoted under it",
            "Sure.\n\nOn 2 Sep 2026, at 09:00, Someone <s@x.io> wrote:",
            "Sure.",
        ),
        ("no quote at all", "What's the salary range?", "What's the salary range?"),
        (
            # Must not eat real content: no address, so it is a sentence, not an attribution.
            "a sentence that opens like an attribution",
            "On Monday I wrote:\nthe brief you asked for, attached.",
            "On Monday I wrote:\nthe brief you asked for, attached.",
        ),
        (
            # Must not eat real content.
            "a colon that isn't an attribution",
            "Here's my question:\nWhat's the range?",
            "Here's my question:\nWhat's the range?",
        ),
    ],
)
def test_quoted_history_is_stripped_from_a_reply(client: str, raw: str, expected: str) -> None:
    """Stored whole, the quote shows the thread to itself twice — and, worse, `classify_reply`
    reads *our own* previous message, footer included, as part of what the candidate said."""
    assert receiving.strip_quoted_reply(raw) == expected, client


def test_a_reply_that_is_only_a_quote_keeps_its_text() -> None:
    """A noisy message on the thread is recoverable; an empty one is not."""
    assert receiving.strip_quoted_reply("> nothing but quote") == "> nothing but quote"


@pytest.mark.db
async def test_a_linkedin_reply_is_left_alone(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Chat has no quoted history, so a DM that happens to start a line with ">" must survive."""
    _with_secret(monkeypatch)
    enr = await _thread(db_session, slug="rx-li-quote", chat_id="CHAT-Q")
    dm = "> this is how I'd phrase it\nthoughts?"
    r = await db_client.post(
        f"/webhooks/unipile?token={_SECRET}", json=_event("CHAT-Q", dm, message_id="M-Q")
    )
    assert r.json()["status"] == "queued"
    assert [m.body for m in await _inbound_rows(db_session, enr.id)] == [dm]
