"""Provider delivery seam: seat-aware sends, thread-id capture, InMail, dedupe, hard-bounce, caps.

These lock in the messaging↔provider integration fixes — the send path now routes through the real
Unipile channel from the org's connected seat (not a global account), records the provider thread id
for reply mapping, sends cold LinkedIn as InMail, de-dupes redelivered inbound events, suppresses on
hard bounce, and honors per-seat daily caps.
"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
    Organization,
    SeatType,
    Suppression,
    SuppressionReason,
)
from app.services.outreach import enrollment as enr_service
from app.services.outreach import messaging as msg_service
from app.services.outreach.messaging import (
    PermanentSendError,
    deliver_outbound,
    record_inbound,
    resolve_channel_seat,
)
from tests.factories import make_org, make_user, make_workspace


class FakeChannel:
    """Records send/reply calls in place of a live Unipile channel."""

    def __init__(self, channel: str, *, reply_accepted: bool = True) -> None:
        self.channel = channel
        self.sent: list[dict[str, object]] = []
        self.replied: list[dict[str, object]] = []
        # Mirrors the real client: False is a permanent provider rejection of a reply.
        self.reply_accepted = reply_accepted

    async def send(
        self,
        *,
        account_id: str,
        to: str,
        subject: str | None,
        body: str,
        inmail: bool = False,
        idempotency_key: str | None = None,
    ) -> str | None:
        self.sent.append(
            {
                "account_id": account_id,
                "to": to,
                "inmail": inmail,
                "idempotency_key": idempotency_key,
            }
        )
        return "thread-xyz"

    async def reply(
        self, *, account_id: str, thread_id: str, body: str, idempotency_key: str | None = None
    ) -> bool:
        self.replied.append(
            {"account_id": account_id, "thread_id": thread_id, "idempotency_key": idempotency_key}
        )
        return self.reply_accepted


def _use_channel(monkeypatch: pytest.MonkeyPatch, channel_str: str, fake: FakeChannel) -> None:
    monkeypatch.setattr(
        msg_service, "unipile_channel", lambda ch: fake if ch == channel_str else None
    )


def _live(monkeypatch: pytest.MonkeyPatch, *, linkedin: bool = False, email: bool = False) -> None:
    """Turn off dry-run for a channel so the real transport path runs (reverted after the test)."""
    s = get_settings()
    if linkedin:
        monkeypatch.setattr(s, "linkedin_dry_run", False)
    if email:
        monkeypatch.setattr(s, "email_dry_run", False)


async def _thread(
    session: AsyncSession, *, slug: str = "deliv", email: str = "ada@example.com"
) -> tuple[Organization, Contact, Enrollment]:
    org = await make_org(session, slug=slug)
    ws = await make_workspace(session, org=org)
    camp = Campaign(workspace_id=ws.id, name="C", from_email="rec@x.com", criteria={}, sequence=[])
    contact = Contact(
        workspace_id=ws.id,
        full_name="Ada",
        email=email,
        linkedin_url="https://linkedin.com/in/ada",
        skills=[],
        tags=[],
    )
    session.add_all([camp, contact])
    await session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=camp.id,
        contact_id=contact.id,
        state=EnrollmentState.scheduled,
        score=50,
        next_run_at=datetime.now(UTC),
    )
    session.add(enr)
    await session.flush()
    return org, contact, enr


def _seat(
    org_id: str,
    user_id: str,
    provider: ConnectionProvider,
    *,
    status: ConnectionStatus = ConnectionStatus.ok,
    external_id: str = "acct-1",
    caps: dict[str, object] | None = None,
    # A paid tier by default: InMail needs credits a free ("basic") seat doesn't have, and most of
    # these tests are about the transport rather than the seat's plan.
    seat_type: SeatType = SeatType.recruiter,
) -> Connection:
    return Connection(
        organization_id=org_id,
        user_id=user_id,
        provider=provider,
        external_id=external_id,
        status=status,
        seat_type=seat_type,
        capabilities=caps or {},
    )


def _outbound(enr: Enrollment, channel: Channel, **kw: object) -> Message:
    fields: dict[str, object] = {"status": MessageStatus.approved, "body": "hi"}
    fields.update(kw)
    return Message(
        workspace_id=enr.workspace_id,
        enrollment_id=enr.id,
        direction=MessageDirection.outbound,
        channel=channel,
        **fields,
    )


@pytest.mark.db
async def test_linkedin_send_uses_seat_inmail_and_captures_thread(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, contact, enr = await _thread(db_session)
    _live(monkeypatch, linkedin=True)  # exercise the real provider path
    fake = FakeChannel("linkedin")
    _use_channel(monkeypatch, "linkedin", fake)
    msg = _outbound(enr, Channel.linkedin, idempotency_key="idem-1")
    db_session.add(msg)
    await db_session.flush()
    user = await make_user(db_session)
    seat = _seat(org.id, user.id, ConnectionProvider.linkedin, external_id="acct-li")

    await deliver_outbound(
        db_session,
        message=msg,
        contact=contact,
        seat=seat,
        sender="rec@x.com",
        inmail=True,  # the campaign opted in; InMail is never the default (it needs seat credits)
    )

    assert len(fake.sent) == 1
    assert fake.sent[0]["account_id"] == "acct-li"  # the per-seat account, not a global one
    assert fake.sent[0]["inmail"] is True
    assert fake.sent[0]["idempotency_key"] == "idem-1"  # dedupe key forwarded to the provider
    assert msg.external_id == "thread-xyz"  # provider thread captured for reply mapping
    assert msg.account_id == "acct-li"


@pytest.mark.db
async def test_needs_reauth_seat_is_permanent_failure(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, contact, enr = await _thread(db_session, slug="reauth")
    _live(monkeypatch, linkedin=True)
    fake = FakeChannel("linkedin")
    _use_channel(monkeypatch, "linkedin", fake)
    msg = _outbound(enr, Channel.linkedin)
    db_session.add(msg)
    await db_session.flush()
    user = await make_user(db_session)
    seat = _seat(org.id, user.id, ConnectionProvider.linkedin, status=ConnectionStatus.needs_reauth)
    with pytest.raises(PermanentSendError):
        await deliver_outbound(
            db_session, message=msg, contact=contact, seat=seat, sender="r@x.com"
        )
    assert not fake.sent  # a dead seat never transmits


@pytest.mark.db
async def test_email_reply_threads_via_prior_message_id(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _org, contact, enr = await _thread(db_session, slug="thread")
    captured: dict[str, str | None] = {}

    def fake_sync(
        host: str,
        port: int,
        sender: str,
        to: str,
        subject: str,
        body: str,
        message_id: str,
        in_reply_to: str | None,
        unsub: str | None,
    ) -> None:
        captured["mid"] = message_id
        captured["irt"] = in_reply_to

    monkeypatch.setattr(msg_service, "unipile_channel", lambda ch: None)  # force SMTP fallback
    monkeypatch.setattr(msg_service, "_send_sync", fake_sync)
    _live(monkeypatch, email=True)  # exercise the actual SMTP call

    first = _outbound(enr, Channel.email, status=MessageStatus.sent, external_id="<mid-1@x>")
    reply = _outbound(enr, Channel.email, status=MessageStatus.sent, body="again")
    db_session.add_all([first, reply])
    await db_session.flush()

    await deliver_outbound(
        db_session, message=reply, contact=contact, seat=None, sender="r@x.com", reply=True
    )
    assert captured["irt"] == "<mid-1@x>"  # In-Reply-To threads onto the prior message
    assert reply.external_id == captured["mid"]  # its own Message-ID is recorded


@pytest.mark.db
async def test_inbound_dedupe_by_provider_message_id(db_session: AsyncSession) -> None:
    _org, _contact, enr = await _thread(db_session, slug="dedupe")
    now = datetime.now(UTC)
    first = await record_inbound(
        db_session, enrollment=enr, text="hi", now=now, provider_message_id="evt-1"
    )
    dup = await record_inbound(
        db_session, enrollment=enr, text="hi", now=now, provider_message_id="evt-1"
    )
    assert first is not None and dup is None  # the redelivered event is dropped
    inbound = (
        await db_session.execute(
            select(func.count())
            .select_from(Message)
            .where(Message.enrollment_id == enr.id, Message.direction == MessageDirection.inbound)
        )
    ).scalar_one()
    assert inbound == 1


@pytest.mark.db
async def test_inbound_preserves_linkedin_channel(db_session: AsyncSession) -> None:
    _org, _contact, enr = await _thread(db_session, slug="chan")
    await record_inbound(
        db_session,
        enrollment=enr,
        text="hi",
        now=datetime.now(UTC),
        channel=Channel.linkedin,
        provider_message_id="evt-2",
    )
    m = (
        (
            await db_session.execute(
                select(Message).where(
                    Message.enrollment_id == enr.id,
                    Message.direction == MessageDirection.inbound,
                )
            )
        )
        .scalars()
        .first()
    )
    assert m is not None and m.channel == Channel.linkedin  # not mislabeled as email


@pytest.mark.db
async def test_hard_bounce_suppresses_and_fails(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _org, _contact, enr = await _thread(db_session, slug="bounce", email="bad@example.com")

    async def boom(*_a: object, **_k: object) -> None:
        raise PermanentSendError("bad address", recipient_rejected=True)

    monkeypatch.setattr(enr_service, "deliver_outbound", boom)
    msg = _outbound(enr, Channel.email)
    db_session.add(msg)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))

    assert msg.status == MessageStatus.failed  # hard failure, not a retry
    supp = (
        (
            await db_session.execute(
                select(Suppression).where(Suppression.email == "bad@example.com")
            )
        )
        .scalars()
        .first()
    )
    assert supp is not None and supp.reason == SuppressionReason.bounced


@pytest.mark.db
async def test_a_broken_seat_does_not_suppress_the_candidate(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Only the *address* being rejected is a bounce.

    A dead seat, an unconfigured account or an InMail from a seat with no credits are all hard
    failures, but they are ours — suppressing the candidate over one permanently do-not-contacts
    them org-wide, and nothing in the thread can undo it.
    """
    _org, _contact, enr = await _thread(db_session, slug="seat-fail", email="fine@example.com")

    async def boom(*_a: object, **_k: object) -> None:
        raise PermanentSendError("email seat needs reauthentication")

    monkeypatch.setattr(enr_service, "deliver_outbound", boom)
    msg = _outbound(enr, Channel.email)
    db_session.add(msg)
    await db_session.flush()

    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))

    assert msg.status == MessageStatus.failed  # still a hard failure, still no retry
    supp = (
        (
            await db_session.execute(
                select(Suppression).where(Suppression.email == "fine@example.com")
            )
        )
        .scalars()
        .first()
    )
    assert supp is None


@pytest.mark.db
async def test_per_seat_daily_cap_reached(db_session: AsyncSession) -> None:
    org, _contact, enr = await _thread(db_session, slug="cap")
    user = await make_user(db_session)
    seat = _seat(
        org.id, user.id, ConnectionProvider.linkedin, external_id="acct-cap", caps={"daily_cap": 1}
    )
    db_session.add(seat)
    await db_session.flush()
    now = datetime.now(UTC)
    db_session.add(
        _outbound(
            enr,
            Channel.linkedin,
            status=MessageStatus.sent,
            account_id="acct-cap",
            sent_at=now,
        )
    )
    await db_session.flush()
    assert await enr_service._seat_cap_reached(db_session, seat=seat, now=now) is True


@pytest.mark.db
async def test_linkedin_reply_threads_and_forwards_idempotency_key(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    org, contact, enr = await _thread(db_session, slug="lireply")
    _live(monkeypatch, linkedin=True)
    fake = FakeChannel("linkedin")
    _use_channel(monkeypatch, "linkedin", fake)
    # A prior outbound with a captured thread id → the reply posts into it.
    db_session.add(
        _outbound(enr, Channel.linkedin, status=MessageStatus.sent, external_id="chat-1")
    )
    msg = _outbound(enr, Channel.linkedin, idempotency_key="idem-r")
    db_session.add(msg)
    await db_session.flush()
    user = await make_user(db_session)
    seat = _seat(org.id, user.id, ConnectionProvider.linkedin, external_id="acct-li")

    await deliver_outbound(
        db_session, message=msg, contact=contact, seat=seat, sender="r@x.com", reply=True
    )
    assert fake.sent == []  # replied into the thread, not a fresh InMail
    assert len(fake.replied) == 1
    assert fake.replied[0]["thread_id"] == "chat-1"
    assert fake.replied[0]["idempotency_key"] == "idem-r"  # dedupe on reply retries


@pytest.mark.db
async def test_rejected_linkedin_reply_fails_hard_instead_of_retrying(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A provider that refuses the reply is a permanent failure, not a retry.

    The transport used to swallow the rejection (`reply` returned None either way), so the message
    was stamped `sent` and left a thread bubble for something that never left.
    """
    org, contact, enr = await _thread(db_session, slug="lireject")
    _live(monkeypatch, linkedin=True)
    _use_channel(monkeypatch, "linkedin", FakeChannel("linkedin", reply_accepted=False))
    db_session.add(
        _outbound(enr, Channel.linkedin, status=MessageStatus.sent, external_id="chat-dead")
    )
    msg = _outbound(enr, Channel.linkedin)
    db_session.add(msg)
    await db_session.flush()
    user = await make_user(db_session, org=org)
    seat = _seat(org.id, user.id, ConnectionProvider.linkedin, external_id="acct-li")

    with pytest.raises(PermanentSendError):
        await deliver_outbound(
            db_session, message=msg, contact=contact, seat=seat, sender="r@x.com", reply=True
        )


@pytest.mark.db
async def test_linkedin_failure_does_not_suppress_email(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A LinkedIn-channel hard failure must NOT suppress the contact's email address.
    _org, _contact, enr = await _thread(db_session, slug="nosupp", email="keep@example.com")

    async def boom(*_a: object, **_k: object) -> None:
        raise PermanentSendError("linkedin unreachable")

    monkeypatch.setattr(enr_service, "deliver_outbound", boom)
    msg = _outbound(enr, Channel.linkedin)
    db_session.add(msg)
    await db_session.flush()
    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))
    assert msg.status == MessageStatus.failed
    supp = (
        (
            await db_session.execute(
                select(Suppression).where(Suppression.email == "keep@example.com")
            )
        )
        .scalars()
        .first()
    )
    assert supp is None  # email untouched by a LinkedIn failure


@pytest.mark.db
async def test_followup_touchpoint_sends_as_reply(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    _org, _contact, enr = await _thread(db_session, slug="followup")
    calls: list[bool] = []

    async def capture(*_a: object, **kw: object) -> None:
        calls.append(bool(kw.get("reply")))

    monkeypatch.setattr(enr_service, "deliver_outbound", capture)
    enr.current_step = 2  # a follow-up, not the opener
    msg = _outbound(enr, Channel.email)
    db_session.add(msg)
    await db_session.flush()
    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))
    assert calls == [True]  # step > 0 replies into the thread


@pytest.mark.db
async def test_duplicate_provider_message_id_rejected(db_session: AsyncSession) -> None:
    # The partial unique index makes a redelivered inbound id un-insertable a second time.
    _org, _contact, enr = await _thread(db_session, slug="uniq")

    def _inbound() -> Message:
        return Message(
            workspace_id=enr.workspace_id,
            enrollment_id=enr.id,
            direction=MessageDirection.inbound,
            channel=Channel.email,
            status=MessageStatus.received,
            body="hi",
            provider_message_id="evt-dup",
        )

    db_session.add(_inbound())
    await db_session.flush()
    with pytest.raises(IntegrityError):
        async with db_session.begin_nested():  # isolate the failing insert to a savepoint
            db_session.add(_inbound())
            await db_session.flush()


@pytest.mark.db
async def test_linkedin_falls_back_to_email_when_no_seat(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No connected LinkedIn account (and not simulating) → the touchpoint routes over email.
    _org, _contact, enr = await _thread(db_session, slug="fallback")
    _live(monkeypatch, linkedin=True)
    msg = _outbound(enr, Channel.linkedin)
    db_session.add(msg)
    await db_session.flush()
    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))
    assert msg.channel == Channel.email  # re-routed
    assert (
        msg.status == MessageStatus.sent
    )  # delivered (email dry-run), not a phantom LinkedIn send


@pytest.mark.db
async def test_linkedin_no_seat_no_email_fails_visibly(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    # No LinkedIn account AND no email to fall back to → fail visibly, never a phantom "sent".
    _org, _contact, enr = await _thread(db_session, slug="noemail", email="")
    _live(monkeypatch, linkedin=True)
    msg = _outbound(enr, Channel.linkedin)
    db_session.add(msg)
    await db_session.flush()
    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))
    assert msg.status == MessageStatus.failed
    assert msg.channel == Channel.linkedin  # unchanged; nothing was sent


@pytest.mark.db
async def test_linkedin_dryrun_still_simulates(db_session: AsyncSession) -> None:
    # In dry-run (demo default) a LinkedIn touchpoint with no seat simulates as sent, unchanged.
    _org, _contact, enr = await _thread(db_session, slug="lidry")  # LINKEDIN_DRY_RUN stays "1"
    msg = _outbound(enr, Channel.linkedin)
    db_session.add(msg)
    await db_session.flush()
    await enr_service.tick(db_session, enrollment=enr, now=datetime.now(UTC))
    assert msg.status == MessageStatus.sent
    assert msg.channel == Channel.linkedin  # no fallback in dry-run


@pytest.mark.db
async def test_resolve_channel_seat_prefers_the_campaign_seat(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="seatpick")
    ws = await make_workspace(db_session, org=org)
    creator = await make_user(db_session, email="creator@x.com")
    other = await make_user(db_session, email="other@x.com")
    designated = _seat(org.id, other.id, ConnectionProvider.linkedin, external_id="designated")
    creators = _seat(org.id, creator.id, ConnectionProvider.linkedin, external_id="creators")
    db_session.add_all([designated, creators])
    await db_session.flush()
    camp = Campaign(
        workspace_id=ws.id,
        name="C",
        sequence=[],
        created_by_user_id=creator.id,
        seat_id=designated.id,
    )
    db_session.add(camp)
    await db_session.flush()

    seat = await resolve_channel_seat(db_session, campaign=camp, channel=Channel.linkedin)
    assert seat is not None and seat.external_id == "designated"

    # An unhealthy designated seat is never used; resolution drops to the creator's seat.
    designated.status = ConnectionStatus.needs_reauth
    await db_session.flush()
    seat = await resolve_channel_seat(db_session, campaign=camp, channel=Channel.linkedin)
    assert seat is not None and seat.external_id == "creators"

    # A designated seat on the wrong channel is ignored too.
    assert await resolve_channel_seat(db_session, campaign=camp, channel=Channel.email) is None


@pytest.mark.db
async def test_resolve_channel_seat_never_borrows_a_colleagues_seat(
    db_session: AsyncSession,
) -> None:
    org = await make_org(db_session, slug="seatborrow")
    ws = await make_workspace(db_session, org=org)
    creator = await make_user(db_session, email="nc@x.com")
    colleague = await make_user(db_session, email="colleague@x.com")
    db_session.add(_seat(org.id, colleague.id, ConnectionProvider.linkedin, external_id="theirs"))
    camp = Campaign(workspace_id=ws.id, name="C", sequence=[], created_by_user_id=creator.id)
    db_session.add(camp)
    await db_session.flush()

    assert await resolve_channel_seat(db_session, campaign=camp, channel=Channel.linkedin) is None
