"""Enrollment state machine (service).

`tick()` advances one enrollment by exactly one transition; the worker (or the admin run-due
endpoint) calls it for every enrollment whose `next_run_at` is due. `state` + `next_run_at` are the
source of truth — no external scheduler.

Flow:
    proposed --approve--> active
    active     -> draft a touchpoint; auto-mode approves it (scheduled) else awaiting_approval
    (message approved) -> scheduled
    scheduled  -> send the approved touchpoint, advance step, wait (awaiting_reply)
    awaiting_reply -> next touchpoint due? back to active : completed
    (inbound reply) -> handed_off (interested) | opted_out  [handled in messaging service]
"""

from datetime import datetime, timedelta

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import policy
from app.core.config import get_settings
from app.core.db import new_id
from app.core.types import JsonList
from app.models import (
    TERMINAL,
    AutonomyLevel,
    Campaign,
    Channel,
    Connection,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    SuppressionReason,
    Workspace,
)
from app.services.outreach import governor
from app.services.outreach.messaging import (
    PermanentSendError,
    TransientSendError,
    deliver_outbound,
    draft_message,
    linkedin_transport_ready,
    resolve_channel_seat,
    write_message,
)
from app.services.sourcing import suppression

_FINAL_GRACE_DAYS = 3
_MAX_SEND_ATTEMPTS = 3
_BACKOFF = (timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=60))


def _tomorrow(now: datetime) -> datetime:
    """Start of the next UTC day — when a per-seat daily cap resets."""
    return datetime(now.year, now.month, now.day, tzinfo=now.tzinfo) + timedelta(days=1)


async def _seat_cap_reached(session: AsyncSession, *, seat: Connection, now: datetime) -> bool:
    """Has this seat hit its per-account daily send cap (`capabilities.daily_cap`)?"""
    caps = seat.capabilities or {}
    cap = caps.get("daily_cap")
    if not isinstance(cap, int) or cap <= 0:
        return False
    start = datetime(now.year, now.month, now.day, tzinfo=now.tzinfo)
    sent_today = (
        await session.execute(
            select(func.count())
            .select_from(Message)
            .where(
                Message.account_id == seat.external_id,
                Message.status == MessageStatus.sent,
                Message.sent_at >= start,
            )
        )
    ).scalar_one()
    return int(sent_today) >= cap


# --- State machine -----------------------------------------------------------


def _advance(enrollment: Enrollment, sequence: JsonList, now: datetime) -> None:
    """Move to the next touchpoint (or the post-sequence grace wait)."""
    enrollment.current_step += 1
    enrollment.state = EnrollmentState.awaiting_reply
    if enrollment.current_step < len(sequence):
        raw_delay = sequence[enrollment.current_step].get("delay_days", 0)
        delay = int(raw_delay) if isinstance(raw_delay, int | float | str) else 0
        enrollment.next_run_at = now + timedelta(days=delay)
    else:
        enrollment.next_run_at = now + timedelta(days=_FINAL_GRACE_DAYS)


async def approve_enrollment(
    session: AsyncSession, *, workspace_id: str, enrollment_id: str, now: datetime
) -> Enrollment:
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="enrollment not found")
    if enrollment.state != EnrollmentState.proposed:
        raise HTTPException(status_code=409, detail="enrollment is not awaiting approval")
    enrollment.state = EnrollmentState.active
    enrollment.next_run_at = now
    await session.flush()
    return enrollment


async def list_for_campaign(
    session: AsyncSession,
    *,
    campaign_id: str,
    state: EnrollmentState | None = None,
) -> list[Enrollment]:
    stmt = select(Enrollment).where(Enrollment.campaign_id == campaign_id)
    if state is not None:
        stmt = stmt.where(Enrollment.state == state)
    stmt = stmt.order_by(Enrollment.score.desc())
    return list[Enrollment]((await session.execute(stmt)).scalars().all())


async def tick(session: AsyncSession, *, enrollment: Enrollment, now: datetime) -> None:
    campaign = (
        await session.get(Campaign, enrollment.campaign_id) if enrollment.campaign_id else None
    )
    contact = await session.get(Contact, enrollment.contact_id)
    if campaign is None or contact is None:
        enrollment.state = EnrollmentState.completed
        enrollment.next_run_at = None
        return
    sequence = campaign.sequence or []

    if enrollment.state == EnrollmentState.active:
        await _draft_touchpoint(session, enrollment, campaign, contact, sequence, now)
    elif enrollment.state == EnrollmentState.scheduled:
        await _send_touchpoint(session, enrollment, campaign, contact, sequence, now)
    elif enrollment.state == EnrollmentState.awaiting_reply:
        if enrollment.reply_pending:
            # A reply is waiting to be handled — don't fire the next touchpoint over it. Re-check
            # later; once the reply is handled (reply_pending cleared) the sequence resumes.
            enrollment.next_run_at = now + timedelta(days=_FINAL_GRACE_DAYS)
        elif enrollment.current_step < len(sequence):
            enrollment.state = EnrollmentState.active
            enrollment.next_run_at = now
        else:
            enrollment.state = EnrollmentState.completed
            enrollment.next_run_at = None
    await session.flush()


async def _draft_touchpoint(
    session: AsyncSession,
    enrollment: Enrollment,
    campaign: Campaign,
    contact: Contact,
    sequence: JsonList,
    now: datetime,
) -> None:
    if enrollment.current_step >= len(sequence):
        enrollment.state = EnrollmentState.completed
        enrollment.next_run_at = None
        return
    step = sequence[enrollment.current_step]
    channel = Channel.linkedin if step.get("channel") == "linkedin" else Channel.email
    voice = (await policy.for_campaign(session, campaign=campaign)).get_str("brand_voice")
    subject, body = await draft_message(contact, step, brand_voice=voice or None)
    message = Message(
        workspace_id=enrollment.workspace_id,
        enrollment_id=enrollment.id,
        direction=MessageDirection.outbound,
        channel=channel,
        status=MessageStatus.draft,
        # LinkedIn carries no subject: the transport drops it, so keeping one would show the
        # recruiter a subject line in the thread and the approval preview that is never sent.
        subject=subject if channel == Channel.email else None,
        body=body,
    )
    session.add(message)
    await session.flush()

    # Gate on autonomy_level, the one field every gate reads.
    if campaign.autonomy_level == AutonomyLevel.full:
        message.status = MessageStatus.approved
        if not message.idempotency_key:
            message.idempotency_key = new_id()
        enrollment.state = EnrollmentState.scheduled
        enrollment.next_run_at = now
        # When this is expected to go out. The column and the "Scheduled — sends <date>" entry on
        # the contact timeline both already existed, but only the demo seeder ever wrote it, so
        # outside the seeded workspace a queued touchpoint never showed a send time.
        message.scheduled_at = now
    else:
        enrollment.state = EnrollmentState.awaiting_approval
        enrollment.next_run_at = None


async def _send_touchpoint(
    session: AsyncSession,
    enrollment: Enrollment,
    campaign: Campaign,
    contact: Contact,
    sequence: JsonList,
    now: datetime,
) -> None:
    message = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.enrollment_id == enrollment.id,
                    Message.status == MessageStatus.approved,
                )
                .order_by(Message.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if message is None:
        # Nothing approved to send — drop back to waiting for approval.
        enrollment.state = EnrollmentState.awaiting_approval
        enrollment.next_run_at = None
        return

    # Org-level do-not-contact gate: never send to a suppressed address.
    workspace = await session.get(Workspace, enrollment.workspace_id)
    org_id = workspace.organization_id if workspace else None
    if org_id and await suppression.is_suppressed(
        session,
        organization_id=org_id,
        email=contact.email,
        workspace_id=enrollment.workspace_id,
    ):
        message.status = MessageStatus.failed
        enrollment.state = EnrollmentState.opted_out
        enrollment.outcome = "opted_out"
        enrollment.next_run_at = None
        return

    seat = await resolve_channel_seat(session, campaign=campaign, channel=message.channel)

    # No connected LinkedIn account for a LinkedIn touchpoint: fall back to email (if the contact
    # has one), else fail the touchpoint visibly — never a phantom "sent". Dry-run still simulates.
    if (
        message.channel == Channel.linkedin
        and not get_settings().linkedin_dry_run
        and not linkedin_transport_ready(seat)
    ):
        if contact.email:
            message.channel = Channel.email
            # A LinkedIn step is drafted with no subject, because LinkedIn has no subject line.
            # Carrying that null across the fallback sent the email with an empty Subject header —
            # a poor first impression and a spam signal, on what is the default fallback path.
            if not message.subject:
                step = sequence[enrollment.current_step] if enrollment.current_step < len(
                    sequence
                ) else {}
                message.subject = write_message(contact, step)[0] or campaign.name
            seat = await resolve_channel_seat(session, campaign=campaign, channel=Channel.email)
        else:
            message.status = MessageStatus.failed
            _advance(enrollment, sequence, now)
            return

    # Rate/window governor: defer (without advancing) if a policy cap or window blocks the send.
    allowed, retry_at = await governor.can_send_now(
        session, campaign=campaign, channel=message.channel, now=now
    )
    if not allowed:
        enrollment.next_run_at = retry_at or (now + timedelta(minutes=15))
        return

    # Per-seat daily cap: LinkedIn/email accounts are throttled per account, not just per workspace.
    if seat is not None and await _seat_cap_reached(session, seat=seat, now=now):
        enrollment.next_run_at = _tomorrow(now)
        return

    sender = campaign.from_email or get_settings().default_from_email
    unsub = suppression.unsubscribe_url(org_id, contact.email) if org_id and contact.email else None
    # Follow-up steps reply into the existing thread (so it threads and, on LinkedIn, doesn't
    # re-InMail); the first touch (step 0) opens the thread.
    is_reply = enrollment.current_step > 0
    try:
        await deliver_outbound(
            session,
            message=message,
            contact=contact,
            seat=seat,
            sender=sender,
            unsubscribe_url=unsub,
            reply=is_reply,
            # The campaign opts in; InMail is never the default. A basic/free seat has no InMail
            # credits, so sending every cold touch as one would fail for most accounts.
            inmail=campaign.use_inmail and message.channel == Channel.linkedin,
        )
        message.status = MessageStatus.sent
        message.sent_at = now
    except PermanentSendError as exc:
        # Hard failure (bad address / dead seat): fail + advance, no retry. Suppress only when the
        # *address* was rejected on the *email* channel — a LinkedIn failure isn't an email bounce,
        # and neither is an unreauthed seat or an unconfigured account. Those are our problem, and
        # burning the candidate's address over one is not recoverable from the thread.
        message.status = MessageStatus.failed
        if org_id and contact.email and message.channel == Channel.email and exc.recipient_rejected:
            await suppression.suppress(
                session,
                organization_id=org_id,
                email=contact.email,
                reason=SuppressionReason.bounced,
                contact_id=contact.id,
            )
        _advance(enrollment, sequence, now)
        return
    except TransientSendError:
        # Transient failure: retry with backoff, advancing only after exhausting attempts.
        message.attempts += 1
        if message.attempts < _MAX_SEND_ATTEMPTS:
            enrollment.next_run_at = now + _BACKOFF[min(message.attempts - 1, len(_BACKOFF) - 1)]
            return
        message.status = MessageStatus.failed
        _advance(enrollment, sequence, now)
        return

    # `deliver_outbound` stamps the provider thread id and the sending seat onto the message
    # itself — inbound replies map back to this thread through them, and the next touchpoint on
    # this channel continues the same conversation.
    _advance(enrollment, sequence, now)


async def close_for_opt_out(
    session: AsyncSession, *, organization_id: str, email: str, now: datetime
) -> int:
    """End every live conversation with an address that has just opted out. Returns how many.

    Clicking the unsubscribe link used to suppress the address and stop there, leaving the thread
    reading "Awaiting reply" — so the clearest signal a candidate can send produced no visible
    change, while a *guessed-at* keyword in a reply closed the conversation outright. The next
    touchpoint would then be attempted anyway and refused at send time, failing the enrollment
    instead of ending it cleanly.
    """
    rows = (
        (
            await session.execute(
                select(Enrollment)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .join(Workspace, Workspace.id == Enrollment.workspace_id)
                .where(
                    Workspace.organization_id == organization_id,
                    func.lower(Contact.email) == email.strip().lower(),
                    Enrollment.state.not_in(TERMINAL),
                )
            )
        )
        .scalars()
        .all()
    )
    for enrollment in rows:
        enrollment.state = EnrollmentState.opted_out
        enrollment.outcome = "opted_out"
        enrollment.next_run_at = None
        enrollment.reply_pending = False
    await session.flush()
    return len(rows)
