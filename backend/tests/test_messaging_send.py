"""Outbound sending (ticket 2a): channel choice, seat resolution, and thread continuation.

Covers the path a message actually takes on the wire — the sequence's touchpoints through the
enrollment state machine, and a recruiter's manual reply through the inbox composer. Unipile is
respx-mocked throughout; nothing here talks to a real provider.
"""

from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastapi import HTTPException
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models import (
    AutonomyLevel,
    Campaign,
    CampaignStatus,
    Channel,
    ConnectionProvider,
    Contact,
    Enrollment,
    EnrollmentState,
    Membership,
    Message,
    MessageDirection,
    MessageStatus,
    Organization,
    SeatType,
    Suppression,
    SuppressionReason,
    Workspace,
)
from app.services.outreach import enrollment as enr_service
from app.services.outreach.messaging import channel_availability, send_conversation_message
from app.services.workspace.connections import upsert_seat
from tests.factories import make_org, make_workspace, make_workspace_member

_DSN = "https://api9.unipile.com:9999"
_PROFILE = "https://linkedin.com/in/leepark"


def _unipile(monkeypatch: pytest.MonkeyPatch) -> None:
    """Point the channel layer at a configured (mocked) Unipile."""
    # The suite sets LINKEDIN_DRY_RUN=1 globally so nothing reaches a provider by accident; these
    # tests are specifically about what goes on the wire, so they opt back in against a mock.
    configured = Settings(unipile_api_key="key", unipile_dsn=_DSN, linkedin_dry_run=False)
    monkeypatch.setattr("app.services.outreach.messaging.get_settings", lambda: configured)
    monkeypatch.setattr("app.ext.unipile.get_settings", lambda: configured)


async def _fixture(
    session: AsyncSession,
    slug: str,
    *,
    seat: bool = True,
    email: str | None = "lee@example.com",
    use_inmail: bool = False,
    seat_type: SeatType = SeatType.recruiter,
) -> tuple[Organization, Workspace, Contact, Campaign]:
    """An org+workspace with a LinkedIn seat, a reachable contact, and a LinkedIn campaign."""
    org = await make_org(session, slug=slug)
    ws = await make_workspace(session, org=org)
    owner: str | None = None
    if seat:
        user = await make_workspace_member(session, org=org, workspace=ws)
        owner = user.id
        await upsert_seat(
            session,
            organization_id=org.id,
            user_id=user.id,
            provider=ConnectionProvider.linkedin,
            account_id="ACCT-1",
            seat_type=seat_type,
        )
    contact = Contact(
        workspace_id=ws.id,
        full_name="Lee Park",
        email=email,
        linkedin_url=_PROFILE,
        skills=[],
        tags=[],
    )
    campaign = Campaign(
        workspace_id=ws.id,
        name="C",
        status=CampaignStatus.active,
        autonomy_level=AutonomyLevel.full,
        criteria={},
        created_by_user_id=owner,
        use_inmail=use_inmail,
        sequence=[
            {"channel": "linkedin", "delay_days": 0, "body": "first touch"},
            {"channel": "linkedin", "delay_days": 0, "body": "second touch"},
        ],
    )
    session.add_all([contact, campaign])
    await session.flush()
    return org, ws, contact, campaign


def _enrollment(ws: Workspace, campaign: Campaign, contact: Contact, now: datetime) -> Enrollment:
    return Enrollment(
        workspace_id=ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.active,
        score=80,
        next_run_at=now,
    )


# --- Sequence touchpoints ----------------------------------------------------


@pytest.mark.db
@respx.mock
async def test_linkedin_touchpoint_sends_from_the_workspace_seat(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The seat's account_id — not the global setting — drives the send, and the chat id is kept."""
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(db_session, "send-li")
    users = respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    chats = respx.post(f"{_DSN}/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"chat_id": "CHAT-1"})
    )
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)  # draft -> scheduled (full auto)
    await enr_service.tick(db_session, enrollment=enr, now=now)  # send

    assert users.calls[0].request.url.params["account_id"] == "ACCT-1"
    assert chats.called
    sent = (
        (
            await db_session.execute(
                select(Message).where(
                    Message.enrollment_id == enr.id, Message.direction == "outbound"
                )
            )
        )
        .scalars()
        .all()
    )
    assert [m.status for m in sent] == [MessageStatus.sent]
    assert sent[0].external_id == "CHAT-1"  # the reply-mapping key for the inbound webhook
    assert sent[0].account_id == "ACCT-1"


@pytest.mark.db
@respx.mock
async def test_second_touchpoint_continues_the_same_chat(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A follow-up lands in the existing LinkedIn chat instead of opening a second one."""
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(db_session, "send-li2")
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    new_chat = respx.post(f"{_DSN}/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"chat_id": "CHAT-1"})
    )
    in_chat = respx.post(f"{_DSN}/api/v1/chats/CHAT-1/messages").mock(
        return_value=httpx.Response(200, json={})
    )
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    # draft -> send -> resume -> draft -> send
    for _ in range(5):
        await enr_service.tick(db_session, enrollment=enr, now=now)

    assert new_chat.call_count == 1  # only the first touch opened a chat
    assert in_chat.call_count == 1
    sent = (
        (
            await db_session.execute(
                select(Message).where(
                    Message.enrollment_id == enr.id, Message.status == MessageStatus.sent
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(sent) == 2
    assert {m.external_id for m in sent} == {"CHAT-1"}


@pytest.mark.db
async def test_unconfigured_linkedin_stays_a_dry_run(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With no Unipile config the sequence still completes (QA behaviour), sending nothing."""
    _org, ws, contact, campaign = await _fixture(db_session, "send-dry", seat=False)
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)
    await enr_service.tick(db_session, enrollment=enr, now=now)

    assert enr.state == EnrollmentState.awaiting_reply
    msg = (
        (await db_session.execute(select(Message).where(Message.enrollment_id == enr.id)))
        .scalars()
        .one()
    )
    assert msg.status == MessageStatus.sent and msg.external_id is None


@pytest.mark.db
@respx.mock
async def test_provider_rejection_retries_instead_of_faking_a_send(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider 5xx must not leave a 'sent' message — it schedules a retry.

    Transient and permanent are treated differently on purpose: a 429/5xx is retried with backoff,
    while a 4xx (the recipient is unreachable from this seat) is hopeless and fails the touchpoint
    rather than burning three attempts on it. Both must leave the message not-sent.
    """
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(db_session, "send-fail")
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    respx.post(f"{_DSN}/api/v1/chats").mock(return_value=httpx.Response(503, json={"e": "later"}))
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)
    await enr_service.tick(db_session, enrollment=enr, now=now)

    msg = (
        (await db_session.execute(select(Message).where(Message.enrollment_id == enr.id)))
        .scalars()
        .one()
    )
    assert msg.status == MessageStatus.approved and msg.attempts == 1
    assert msg.sent_at is None
    assert enr.state == EnrollmentState.scheduled
    assert enr.next_run_at is not None and enr.next_run_at > now


@pytest.mark.db
@respx.mock
async def test_a_permanent_rejection_fails_the_touchpoint_without_retrying(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A 4xx means this seat can't reach them — fail and move on, and never claim it was sent."""
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(db_session, "send-fail-hard")
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    respx.post(f"{_DSN}/api/v1/chats").mock(return_value=httpx.Response(422, json={"e": "nope"}))
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)
    await enr_service.tick(db_session, enrollment=enr, now=now)

    msg = (
        (await db_session.execute(select(Message).where(Message.enrollment_id == enr.id)))
        .scalars()
        .first()
    )
    assert msg is not None
    assert msg.status == MessageStatus.failed and msg.sent_at is None


@pytest.mark.db
async def test_configured_unipile_without_a_seat_fails_instead_of_faking_a_send(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The dry-run is for an unconfigured Unipile only — a missing seat must not read as 'sent'."""
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(db_session, "send-noseat", seat=False)
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)
    await enr_service.tick(db_session, enrollment=enr, now=now)

    msg = (
        (await db_session.execute(select(Message).where(Message.enrollment_id == enr.id)))
        .scalars()
        .one()
    )
    assert msg.status == MessageStatus.failed
    assert enr.current_step == 1  # skipped the touchpoint rather than retrying forever


# --- Availability ------------------------------------------------------------


@pytest.mark.db
async def test_availability_reports_why_a_channel_is_closed(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _org, _ws, contact, campaign = await _fixture(db_session, "send-avail", seat=False, email=None)
    by_channel = {
        o.channel: o
        for o in await channel_availability(db_session, campaign=campaign, contact=contact)
    }
    assert not by_channel[Channel.email].available
    assert "email address" in (by_channel[Channel.email].reason or "")
    # A LinkedIn URL is not enough — LinkedIn has no fallback transport, it needs a seat.
    assert not by_channel[Channel.linkedin].available
    assert "connected" in (by_channel[Channel.linkedin].reason or "")


@pytest.mark.db
async def test_availability_opens_linkedin_once_a_seat_is_connected(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _unipile(monkeypatch)
    _org, _ws, contact, campaign = await _fixture(db_session, "send-avail2")
    by_channel = {
        o.channel: o
        for o in await channel_availability(db_session, campaign=campaign, contact=contact)
    }
    assert by_channel[Channel.linkedin].available
    assert by_channel[Channel.linkedin].target == _PROFILE
    assert by_channel[Channel.email].available


# --- The inbox composer (manual send) ----------------------------------------


async def _api_thread(client: AsyncClient, slug: str) -> tuple[dict[str, str], str]:
    """Sign up through the API and return (auth headers, enrollment id) for a live conversation."""
    signup = await client.post(
        "/organizations",
        json={
            "org_name": f"Org {slug}",
            "slug": slug,
            "admin_email": f"admin@{slug}.com",
            "admin_name": "Admin",
        },
    )
    uid = signup.json()["admin_user_id"]
    ws = await client.post(
        "/workspaces", json={"name": "Team", "kind": "team"}, headers={"X-User-Id": uid}
    )
    h = {"X-User-Id": uid, "X-Workspace-Id": ws.json()["id"]}
    await client.post("/contacts/sample", json={"count": 5}, headers=h)
    cid = (
        await client.post(
            "/campaigns",
            json={
                "name": "C",
                "criteria": {"skills": ["python"], "titles": ["engineer"]},
                "sequence": [{"channel": "email", "delay_days": 0, "body": "hi"}],
            },
            headers=h,
        )
    ).json()["id"]
    ranked = await client.post(f"/campaigns/{cid}/rank", headers=h)
    return h, ranked.json()["enrollments"][0]["id"]


@pytest.mark.db
async def test_composer_lists_both_channels_with_a_default(db_client: AsyncClient) -> None:
    h, eid = await _api_thread(db_client, "compose")
    body = (await db_client.get(f"/inbox/{eid}/channels", headers=h)).json()
    assert {o["channel"] for o in body["options"]} == {"email", "linkedin"}
    # A fresh thread with an email address and no LinkedIn seat defaults to email.
    assert body["default"] == "email"


@pytest.mark.db
async def test_manual_send_records_the_chosen_channel(db_client: AsyncClient) -> None:
    h, eid = await _api_thread(db_client, "compose-send")
    resp = await db_client.post(
        f"/inbox/{eid}/reply",
        json={"text": "Following up!", "channel": "email", "subject": "Quick one"},
        headers=h,
    )
    assert resp.status_code == 200
    sent = resp.json()
    assert sent["channel"] == "email" and sent["status"] == "sent"
    assert sent["subject"] == "Quick one"
    thread = (await db_client.get(f"/enrollments/{eid}/messages", headers=h)).json()
    assert [m["body"] for m in thread] == ["Following up!"]


@pytest.mark.db
async def test_manual_send_on_an_unreachable_channel_is_rejected(db_client: AsyncClient) -> None:
    """No LinkedIn seat → the send is refused, and no phantom message lands in the thread."""
    h, eid = await _api_thread(db_client, "compose-li")
    resp = await db_client.post(
        f"/inbox/{eid}/reply", json={"text": "hello", "channel": "linkedin"}, headers=h
    )
    assert resp.status_code == 422
    thread = (await db_client.get(f"/enrollments/{eid}/messages", headers=h)).json()
    assert thread == []


@pytest.mark.db
async def test_manual_email_to_a_suppressed_contact_is_refused(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    h, eid = await _api_thread(db_client, "compose-supp")
    enr = await db_session.get(Enrollment, eid)
    assert enr is not None
    contact = await db_session.get(Contact, enr.contact_id)
    ws = await db_session.get(Workspace, enr.workspace_id)
    assert contact is not None and contact.email and ws is not None
    db_session.add(
        Suppression(
            organization_id=ws.organization_id,
            email=contact.email,
            reason=SuppressionReason.opted_out,
        )
    )
    await db_session.flush()

    resp = await db_client.post(
        f"/inbox/{eid}/reply", json={"text": "hello", "channel": "email"}, headers=h
    )
    assert resp.status_code == 409
    assert "opted out" in resp.json()["detail"]


@pytest.mark.db
async def test_manual_send_clears_the_pending_reply_flag(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    h, eid = await _api_thread(db_client, "compose-pending")
    enr = await db_session.get(Enrollment, eid)
    assert enr is not None
    enr.reply_pending = True
    await db_session.flush()

    await db_client.post(f"/inbox/{eid}/reply", json={"text": "on it"}, headers=h)
    await db_session.refresh(enr)
    assert enr.reply_pending is False


@pytest.mark.db
async def test_reply_defaults_to_the_channel_the_thread_is_on(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An inbound LinkedIn message makes LinkedIn the default — when a seat can carry it."""
    _unipile(monkeypatch)
    h, eid = await _api_thread(db_client, "compose-default")
    enr = await db_session.get(Enrollment, eid)
    assert enr is not None
    contact = await db_session.get(Contact, enr.contact_id)
    ws = await db_session.get(Workspace, enr.workspace_id)
    assert contact is not None and ws is not None
    contact.linkedin_url = _PROFILE
    # The seat has to belong to the campaign's creator — the API caller — because that is who
    # `resolve_channel_seat` falls back to.
    await upsert_seat(
        db_session,
        organization_id=ws.organization_id,
        user_id=h["X-User-Id"],
        provider=ConnectionProvider.linkedin,
        account_id="ACCT-9",
    )
    db_session.add(
        Message(
            workspace_id=ws.id,
            enrollment_id=eid,
            direction=MessageDirection.inbound,
            channel=Channel.linkedin,
            status=MessageStatus.received,
            body="hey there",
            created_at=datetime.now(UTC),
        )
    )
    await db_session.flush()

    body = (await db_client.get(f"/inbox/{eid}/channels", headers=h)).json()
    assert body["default"] == "linkedin"


@pytest.mark.db
@respx.mock
async def test_manual_linkedin_send_posts_to_unipile(
    db_client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The composer's LinkedIn option really opens a chat, and the chat id lands on the message."""
    _unipile(monkeypatch)
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-7"})
    )
    chats = respx.post(f"{_DSN}/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"chat_id": "CHAT-7"})
    )
    h, eid = await _api_thread(db_client, "compose-li-ok")
    enr = await db_session.get(Enrollment, eid)
    assert enr is not None
    contact = await db_session.get(Contact, enr.contact_id)
    ws = await db_session.get(Workspace, enr.workspace_id)
    assert contact is not None and ws is not None
    contact.linkedin_url = _PROFILE
    await upsert_seat(
        db_session,
        organization_id=ws.organization_id,
        user_id=h["X-User-Id"],
        provider=ConnectionProvider.linkedin,
        account_id="ACCT-7",
    )
    await db_session.flush()

    resp = await db_client.post(
        f"/inbox/{eid}/reply", json={"text": "Hi Lee!", "channel": "linkedin"}, headers=h
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["channel"] == "linkedin" and resp.json()["status"] == "sent"
    assert chats.called
    sent = (
        (
            await db_session.execute(
                select(Message).where(
                    Message.enrollment_id == eid, Message.channel == Channel.linkedin
                )
            )
        )
        .scalars()
        .one()
    )
    assert sent.external_id == "CHAT-7" and sent.account_id == "ACCT-7"


# --- InMail ------------------------------------------------------------------
#
# InMail reaches people the seat isn't connected to, spends the seat's own finite LinkedIn
# credits, and bills at double an ordinary send — so it happens only when the campaign asks for
# it, and only on the message that opens the conversation.


def _sent_as_inmail(request: httpx.Request) -> bool:
    """Whether a multipart `POST /chats` carried the InMail flag."""
    return b'name="linkedin[inmail]"' in request.content


@pytest.mark.db
@respx.mock
async def test_campaign_inmail_flag_reaches_the_wire_and_the_message_row(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """use_inmail on the campaign sends the opening touchpoint as an InMail, and records that."""
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(db_session, "send-inmail", use_inmail=True)
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    chats = respx.post(f"{_DSN}/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"chat_id": "CHAT-1"})
    )
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)  # draft -> scheduled (full auto)
    await enr_service.tick(db_session, enrollment=enr, now=now)  # send

    assert _sent_as_inmail(chats.calls[0].request)
    sent = (
        (
            await db_session.execute(
                select(Message).where(
                    Message.enrollment_id == enr.id, Message.status == MessageStatus.sent
                )
            )
        )
        .scalars()
        .all()
    )
    # Billing reads is_inmail, so it has to reflect the send that actually happened.
    assert [m.is_inmail for m in sent] == [True]


@pytest.mark.db
@respx.mock
async def test_a_normal_campaign_never_sends_an_inmail(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without the opt-in it's a plain DM — the flag is absent and the row bills at the DM rate."""
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(db_session, "send-dm")
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    chats = respx.post(f"{_DSN}/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"chat_id": "CHAT-1"})
    )
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)
    await enr_service.tick(db_session, enrollment=enr, now=now)

    assert not _sent_as_inmail(chats.calls[0].request)
    sent = (
        (await db_session.execute(select(Message).where(Message.enrollment_id == enr.id)))
        .scalars()
        .all()
    )
    assert all(not m.is_inmail for m in sent)


@pytest.mark.db
@respx.mock
async def test_inmail_does_not_apply_to_a_follow_up_in_an_open_chat(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The second touchpoint continues the chat — LinkedIn has no InMail reply, and we don't bill
    one. Only the opening message on an InMail campaign is an InMail."""
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(db_session, "send-inmail2", use_inmail=True)
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    respx.post(f"{_DSN}/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"chat_id": "CHAT-1"})
    )
    in_chat = respx.post(f"{_DSN}/api/v1/chats/CHAT-1/messages").mock(
        return_value=httpx.Response(200, json={})
    )
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    for _ in range(5):  # draft -> send -> resume -> draft -> send
        await enr_service.tick(db_session, enrollment=enr, now=now)

    assert in_chat.called
    assert b"inmail" not in in_chat.calls[0].request.content
    sent = (
        (
            await db_session.execute(
                select(Message)
                .where(Message.enrollment_id == enr.id, Message.status == MessageStatus.sent)
                .order_by(Message.created_at)
            )
        )
        .scalars()
        .all()
    )
    assert [m.is_inmail for m in sent] == [True, False]


@pytest.mark.db
@respx.mock
async def test_inmail_from_a_seat_without_credits_fails_with_the_real_reason(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A free/basic LinkedIn account has no InMail credits, so LinkedIn rejects the send outright.

    Left to the provider that comes back as a bare 4xx and surfaces as "recipient unreachable" —
    which points at the candidate instead of at the seat. Refuse it up front, and say which of the
    two fixes is needed.
    """
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(
        db_session, "send-inmail-basic", use_inmail=True, seat_type=SeatType.basic
    )
    chats = respx.post(f"{_DSN}/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"chat_id": "CHAT-1"})
    )
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)  # draft
    await enr_service.tick(db_session, enrollment=enr, now=now)  # send

    assert not chats.called, "nothing should reach LinkedIn — the seat can't carry an InMail"
    msg = (
        (await db_session.execute(select(Message).where(Message.enrollment_id == enr.id)))
        .scalars()
        .first()
    )
    assert msg is not None
    assert msg.status == MessageStatus.failed and msg.sent_at is None


@pytest.mark.db
@respx.mock
async def test_a_paid_seat_may_send_an_inmail(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The guard is about the tier, not about InMail itself — a Recruiter seat goes through."""
    _unipile(monkeypatch)
    _org, ws, contact, campaign = await _fixture(
        db_session, "send-inmail-paid", use_inmail=True, seat_type=SeatType.recruiter
    )
    respx.get(f"{_DSN}/api/v1/users/leepark").mock(
        return_value=httpx.Response(200, json={"provider_id": "PID-1"})
    )
    chats = respx.post(f"{_DSN}/api/v1/chats").mock(
        return_value=httpx.Response(200, json={"chat_id": "CHAT-1"})
    )
    now = datetime.now(UTC)
    enr = _enrollment(ws, campaign, contact, now)
    db_session.add(enr)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=now)
    await enr_service.tick(db_session, enrollment=enr, now=now)

    assert _sent_as_inmail(chats.calls[0].request)


# --- "Message this person" opens *their* thread ---------------------------------


@pytest.mark.db
async def test_messaging_a_contact_with_no_history_opens_an_empty_thread(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """The bug: Message navigated to a bare `/inbox`, which auto-selects the first row — so it
    opened a conversation with whoever happened to be at the top of the list, not the person
    whose page you were on."""
    h, _eid = await _api_thread(db_client, "open-fresh")
    fresh = await db_client.post(
        "/contacts/import",
        json={"contacts": [{"full_name": "Nina Ray", "email": "nina@x.com"}]},
        headers=h,
    )
    assert fresh.status_code == 200, fresh.text
    contact_id = fresh.json()["contacts"][0]["id"]

    opened = await db_client.post(f"/contacts/{contact_id}/conversation", headers=h)
    assert opened.status_code == 200, opened.text
    eid = opened.json()["enrollment_id"]

    conv = await db_client.get(f"/inbox/{eid}", headers=h)
    assert conv.status_code == 200
    body = conv.json()
    assert body["contact"]["id"] == contact_id  # *their* thread
    assert body["messages"] == []  # ...and it's empty, as it should be
    assert body["campaign"]["id"] is None  # direct: no sequence behind it

    # An enrollment with no messages isn't in the inbox list, so opening one and walking away
    # leaves nothing behind.
    listed = (await db_client.get("/inbox", headers=h)).json()
    assert eid not in [it["enrollment_id"] for it in listed]


@pytest.mark.db
async def test_messaging_the_same_person_twice_reuses_one_thread(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """Otherwise every click of Message forks another thread with the same person, and half the
    conversation ends up in each."""
    h, _eid = await _api_thread(db_client, "open-twice")
    made = await db_client.post(
        "/contacts/import",
        json={"contacts": [{"full_name": "Nina Ray", "email": "nina2@x.com"}]},
        headers=h,
    )
    contact_id = made.json()["contacts"][0]["id"]

    first = (await db_client.post(f"/contacts/{contact_id}/conversation", headers=h)).json()
    again = (await db_client.post(f"/contacts/{contact_id}/conversation", headers=h)).json()
    assert first["enrollment_id"] == again["enrollment_id"]


@pytest.mark.db
async def test_messaging_someone_already_in_a_campaign_opens_that_thread(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """ "Talk to this person" means the conversation they're already in — splitting it across a
    campaign thread and a direct one is how half of it goes missing."""
    h, eid = await _api_thread(db_client, "open-existing")
    enr = await db_session.get(Enrollment, eid)
    assert enr is not None

    opened = await db_client.post(f"/contacts/{enr.contact_id}/conversation", headers=h)
    assert opened.json()["enrollment_id"] == eid


@pytest.mark.db
async def test_a_contact_from_another_workspace_is_not_reachable(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    h_a, _ = await _api_thread(db_client, "open-tenant-a")
    _h_b, eid_b = await _api_thread(db_client, "open-tenant-b")
    enr_b = await db_session.get(Enrollment, eid_b)
    assert enr_b is not None

    r = await db_client.post(f"/contacts/{enr_b.contact_id}/conversation", headers=h_a)
    assert r.status_code == 404


@pytest.mark.db
async def test_a_direct_conversation_can_actually_be_sent_on(db_client: AsyncClient) -> None:
    """The bug: `open_direct_conversation` makes an enrollment with no campaign, but the composer's
    two endpoints both ended their campaign lookup with a 404 — so every thread opened by "Message"
    on a contact could be read and never replied to."""
    h, _ = await _api_thread(db_client, "direct-send")
    made = await db_client.post(
        "/contacts/import",
        json={"contacts": [{"full_name": "Nina Ray", "email": "nina-direct@x.com"}]},
        headers=h,
    )
    contact_id = made.json()["contacts"][0]["id"]
    eid = (await db_client.post(f"/contacts/{contact_id}/conversation", headers=h)).json()[
        "enrollment_id"
    ]

    channels = await db_client.get(f"/inbox/{eid}/channels", headers=h)
    assert channels.status_code == 200, channels.text
    assert channels.json()["default"] == "email"
    # No campaign means no seat to designate, so LinkedIn has no transport to offer.
    by_channel = {o["channel"]: o for o in channels.json()["options"]}
    assert by_channel["email"]["available"] is True

    sent = await db_client.post(f"/inbox/{eid}/reply", json={"text": "hi there"}, headers=h)
    assert sent.status_code == 200, sent.text
    assert sent.json()["body"] == "hi there"

    thread = (await db_client.get(f"/inbox/{eid}", headers=h)).json()
    assert [m["body"] for m in thread["messages"]] == ["hi there"]
    assert thread["campaign"]["id"] is None


@pytest.mark.db
async def test_a_campaign_cannot_send_from_another_orgs_seat(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """`seat_id` came straight off the request body and `resolve_channel_seat` loads it by primary
    key alone — so a campaign could name any connection in the database and send from it: another
    customer's LinkedIn profile or mailbox, spending their InMail credits."""
    h_a, _ = await _api_thread(db_client, "idor-a")
    h_b, _ = await _api_thread(db_client, "idor-b")
    uid_b = h_b["X-User-Id"]
    org_b = (
        (
            await db_session.execute(
                select(Membership.organization_id).where(Membership.user_id == uid_b)
            )
        )
        .scalars()
        .first()
    )
    assert org_b is not None
    victim = await upsert_seat(
        db_session,
        organization_id=org_b,
        user_id=uid_b,
        provider=ConnectionProvider.linkedin,
        account_id="ACCT-VICTIM",
        seat_type=SeatType.recruiter,
    )
    await db_session.commit()

    made = await db_client.post(
        "/campaigns",
        json={"name": "Borrowed", "criteria": {}, "sequence": [], "seat_id": victim.id},
        headers=h_a,
    )
    assert made.status_code == 404, made.text

    mine = await db_client.post(
        "/campaigns", json={"name": "Mine", "criteria": {}, "sequence": []}, headers=h_a
    )
    patched = await db_client.patch(
        f"/campaigns/{mine.json()['id']}", json={"seat_id": victim.id}, headers=h_a
    )
    assert patched.status_code == 404, patched.text
    stored = await db_session.get(Campaign, mine.json()["id"])
    assert stored is not None
    await db_session.refresh(stored)
    assert stored.seat_id is None


@pytest.mark.db
async def test_from_email_is_restricted_to_a_domain_the_team_uses(db_client: AsyncClient) -> None:
    """It lands verbatim in the `From` header on the SMTP path, and nothing checked it — so any
    member could send mail claiming to be any address, another customer's included."""
    h, _ = await _api_thread(db_client, "from-domain")
    spoofed = await db_client.post(
        "/campaigns",
        json={"name": "Spoof", "criteria": {}, "sequence": [], "from_email": "ceo@bigcorp.com"},
        headers=h,
    )
    assert spoofed.status_code == 422, spoofed.text

    malformed = await db_client.post(
        "/campaigns",
        json={"name": "Bad", "criteria": {}, "sequence": [], "from_email": "not-an-address"},
        headers=h,
    )
    assert malformed.status_code == 422

    # The admin signed up as admin@from-domain.com, so that domain is theirs to send from.
    ok = await db_client.post(
        "/campaigns",
        json={
            "name": "Fine",
            "criteria": {},
            "sequence": [],
            "from_email": "recruiting@from-domain.com",
        },
        headers=h,
    )
    assert ok.status_code == 200, ok.text
    assert ok.json()["from_email"] == "recruiting@from-domain.com"


@pytest.mark.db
async def test_an_email_with_no_seat_never_borrows_the_linkedin_account(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`unipile_account_id` is the deployment's connected *LinkedIn* account. Using it as the
    fallback for email too meant an org with no mailbox posted to /emails with a LinkedIn account
    id, the provider 4xx'd, and the candidate's valid address was suppressed as a bounce."""
    configured = Settings(
        unipile_api_key="key",
        unipile_dsn=_DSN,
        unipile_account_id="LINKEDIN-ACCT",
        email_dry_run=True,
        linkedin_dry_run=False,
    )
    monkeypatch.setattr("app.services.outreach.messaging.get_settings", lambda: configured)
    monkeypatch.setattr("app.services.outreach.enrollment.get_settings", lambda: configured)
    monkeypatch.setattr("app.ext.unipile.get_settings", lambda: configured)

    _org, ws, contact, campaign = await _fixture(db_session, "email-noseat")
    campaign.sequence = [{"channel": "email", "delay_days": 0, "subject": "S", "body": "hi"}]
    campaign.autonomy_level = AutonomyLevel.full
    await db_session.flush()

    with respx.mock:
        emails = respx.post(f"{_DSN}/api/v1/emails")
        enr = Enrollment(
            workspace_id=ws.id,
            campaign_id=campaign.id,
            contact_id=contact.id,
            state=EnrollmentState.active,
            next_run_at=datetime.now(UTC),
        )
        db_session.add(enr)
        await db_session.flush()
        await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))
        await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))
        assert not emails.called  # falls through to SMTP instead

    msg = (
        (await db_session.execute(select(Message).where(Message.enrollment_id == enr.id)))
        .scalars()
        .first()
    )
    assert msg is not None and msg.status is MessageStatus.sent
    supp = (
        (await db_session.execute(select(Suppression).where(Suppression.email == contact.email)))
        .scalars()
        .first()
    )
    assert supp is None, "a valid address was suppressed over a missing seat"


@pytest.mark.db
async def test_the_linkedin_to_email_fallback_carries_a_subject(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A LinkedIn step is drafted with no subject, because LinkedIn has no subject line. Carrying
    that null across the fallback sent the email with an empty Subject header."""
    live = Settings(linkedin_dry_run=False, email_dry_run=True)
    monkeypatch.setattr("app.services.outreach.enrollment.get_settings", lambda: live)
    monkeypatch.setattr("app.services.outreach.messaging.get_settings", lambda: live)

    _org, ws, contact, campaign = await _fixture(db_session, "li-fallback", seat=False)
    campaign.sequence = [
        {"channel": "linkedin", "delay_days": 0, "subject": "", "body": "hi {first_name}"}
    ]
    campaign.autonomy_level = AutonomyLevel.full
    await db_session.flush()

    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.active,
        next_run_at=datetime.now(UTC),
    )
    db_session.add(enr)
    await db_session.flush()
    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))
    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))

    msg = (
        (await db_session.execute(select(Message).where(Message.enrollment_id == enr.id)))
        .scalars()
        .first()
    )
    assert msg is not None
    assert msg.channel is Channel.email  # no LinkedIn transport, so it fell back
    assert msg.subject, "fell back to email with an empty subject line"


@pytest.mark.db
async def test_an_opted_out_contact_is_unreachable_on_linkedin_too(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Suppression is keyed on an address, but it records that this *person* asked not to be
    contacted — including via the agent's own `opt_out` tool. Gating the check on `channel ==
    email` let the composer and the agent keep messaging them on LinkedIn, while the sequence's
    own touchpoints (which check unconditionally) correctly refused."""
    _unipile(monkeypatch)
    org, ws, contact, campaign = await _fixture(db_session, "supp-li")
    db_session.add(
        Suppression(
            organization_id=org.id,
            email=contact.email,
            reason=SuppressionReason.opted_out,
        )
    )
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=campaign.id,
        contact_id=contact.id,
        state=EnrollmentState.awaiting_reply,
    )
    db_session.add(enr)
    await db_session.flush()

    with pytest.raises(HTTPException) as caught:
        await send_conversation_message(
            db_session,
            workspace_id=ws.id,
            enrollment=enr,
            campaign=campaign,
            contact=contact,
            channel=Channel.linkedin,
            subject=None,
            body="one more thing",
            sender="rec@x.com",
            organization_id=org.id,
            now=datetime.now(UTC),
        )
    assert caught.value.status_code == 409
    assert "opted out" in str(caught.value.detail)
