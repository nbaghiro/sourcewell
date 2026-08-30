"""Messaging: channels, Writer/Responder agents, and service.

Each agent function has a deterministic baseline (template fill / keyword intent) and an async
Claude-backed variant that falls back to the baseline when the model is unconfigured or errors.
"""

import asyncio
import smtplib
from datetime import datetime
from email.message import EmailMessage
from email.utils import make_msgid

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import llm
from app.core.config import get_settings
from app.core.db import new_id
from app.core.types import JsonList, JsonObject
from app.ext.unipile import unipile_channel
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
    SuppressionReason,
    Workspace,
)
from app.services.sourcing import suppression

# --- Channels (email via SMTP → Mailpit; LinkedIn via Unipile) ---------------
#
# Set EMAIL_DRY_RUN=1 to skip the SMTP call (tests do this). LinkedIn is a no-op unless a Unipile
# key is configured, so multichannel sequences still complete in QA.


class PermanentSendError(Exception):
    """A hard send failure (bad recipient, dead/unreauthed seat) — suppress + fail, don't retry."""


class TransientSendError(Exception):
    """A temporary send failure (network / provider 5xx) — retry with backoff."""


_EMAIL_PROVIDERS = (ConnectionProvider.gmail, ConnectionProvider.graph)


def _providers_for(channel: Channel) -> list[ConnectionProvider]:
    return [ConnectionProvider.linkedin] if channel == Channel.linkedin else list(_EMAIL_PROVIDERS)


async def resolve_channel_seat(
    session: AsyncSession, *, campaign: Campaign, channel: Channel
) -> Connection | None:
    """The seat this campaign sends from on `channel`: its designated seat, else the creator's.

    Returns None when neither resolves — the caller must fail visibly rather than borrow an
    unrelated colleague's mailbox. An unhealthy seat is never returned.
    """
    providers = _providers_for(channel)
    if campaign.seat_id is not None:
        seat = await session.get(Connection, campaign.seat_id)
        if seat is not None and seat.status == ConnectionStatus.ok and seat.provider in providers:
            return seat
    if campaign.created_by_user_id is None:
        return None
    return (
        (
            await session.execute(
                select(Connection)
                .where(
                    Connection.user_id == campaign.created_by_user_id,
                    Connection.provider.in_(providers),
                    Connection.status == ConnectionStatus.ok,
                )
                .order_by(Connection.created_at)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


def linkedin_transport_ready(seat: Connection | None) -> bool:
    """True when there's a real LinkedIn transport to send on: Unipile configured and a usable
    account id (the resolved seat, or the global fallback account). False → no LinkedIn account."""
    account_id = (seat.external_id if seat else None) or get_settings().unipile_account_id
    return unipile_channel("linkedin") is not None and bool(account_id)


async def _last_thread_ref(session: AsyncSession, *, enrollment_id: str) -> str | None:
    """The newest outbound provider thread/message id in this thread (for reply threading)."""
    return (
        (
            await session.execute(
                select(Message.external_id)
                .where(
                    Message.enrollment_id == enrollment_id,
                    Message.direction == MessageDirection.outbound,
                    Message.external_id.is_not(None),
                )
                .order_by(Message.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


def _send_sync(
    host: str,
    port: int,
    sender: str,
    to: str,
    subject: str,
    body: str,
    message_id: str,
    in_reply_to: str | None,
    unsubscribe_url: str | None,
) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = message_id
    if in_reply_to:  # RFC-5322 threading so the reply lands in the same conversation.
        msg["In-Reply-To"] = in_reply_to
        msg["References"] = in_reply_to
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.send_message(msg)


async def deliver_outbound(
    session: AsyncSession,
    *,
    message: Message,
    contact: Contact,
    seat: Connection | None,
    sender: str,
    unsubscribe_url: str | None = None,
    reply: bool = False,
) -> None:
    """Transmit `message` over its channel from the resolved seat, capturing the provider thread id
    on `message.external_id` (and the seat on `message.account_id`). Uses the real Unipile channel
    when configured; falls back to SMTP for email in dev. Raises PermanentSendError (hard, no retry)
    or TransientSendError (retryable). A no-op only when a channel has no configured transport."""
    channel = message.channel
    target = contact.linkedin_url if channel == Channel.linkedin else contact.email
    if not target:
        raise PermanentSendError(f"contact has no {channel.value} address")

    s = get_settings()
    account_id = (seat.external_id if seat else None) or s.unipile_account_id or None
    message.account_id = account_id
    if not message.idempotency_key:
        message.idempotency_key = new_id()
    provider = unipile_channel(channel.value)
    thread_ref = (
        await _last_thread_ref(session, enrollment_id=message.enrollment_id) if reply else None
    )

    if channel == Channel.linkedin:
        if s.linkedin_dry_run:
            return  # dev/demo: LinkedIn is simulated offline — nothing to send
        if provider is None or not account_id:
            # No connected LinkedIn account: a real failure, not a silent "sent". The touchpoint
            # path routes around this via email fallback; reply paths surface it to the caller.
            raise PermanentSendError("no LinkedIn account connected")
        if seat is not None and seat.status != ConnectionStatus.ok:
            raise PermanentSendError("LinkedIn seat needs reauthentication")
        try:
            if reply and thread_ref:
                await provider.reply(
                    account_id=account_id,
                    thread_id=thread_ref,
                    body=message.body,
                    idempotency_key=message.idempotency_key,
                )
                message.external_id = thread_ref
            else:
                chat_id = await provider.send(
                    account_id=account_id,
                    to=target,
                    subject=message.subject,
                    body=message.body,
                    inmail=True,  # cold recruiting reach → InMail, not a connection-gated chat
                    idempotency_key=message.idempotency_key,
                )
                if chat_id is None:
                    raise PermanentSendError("LinkedIn recipient unreachable")
                message.external_id = chat_id
        except PermanentSendError:
            raise
        except Exception as exc:
            raise TransientSendError(str(exc)) from exc
        return

    # Email: real ESP via Unipile when configured, else SMTP (dev/Mailpit) with threading headers.
    if provider is not None and account_id and not s.email_dry_run:
        if seat is not None and seat.status != ConnectionStatus.ok:
            raise PermanentSendError("email seat needs reauthentication")
        try:
            if reply and thread_ref:
                await provider.reply(
                    account_id=account_id,
                    thread_id=thread_ref,
                    body=message.body,
                    idempotency_key=message.idempotency_key,
                )
                message.external_id = thread_ref
            else:
                mid = await provider.send(
                    account_id=account_id,
                    to=target,
                    subject=message.subject,
                    body=message.body,
                    idempotency_key=message.idempotency_key,
                )
                if mid:
                    message.external_id = mid
        except Exception as exc:
            raise TransientSendError(str(exc)) from exc
        return

    message_id = make_msgid()
    if not s.email_dry_run:
        try:
            await asyncio.to_thread(
                _send_sync,
                s.smtp_host,
                s.smtp_port,
                sender,
                target,
                message.subject or "",
                message.body,
                message_id,
                thread_ref,
                unsubscribe_url,
            )
        except Exception as exc:
            raise TransientSendError(str(exc)) from exc
    message.external_id = message_id


# --- Writer + Responder agents -----------------------------------------------


def _fill(template: str, contact: Contact) -> str:
    first = contact.full_name.split()[0] if contact.full_name else "there"
    return (
        template.replace("{name}", contact.full_name or "there")
        .replace("{first_name}", first)
        .replace("{company}", contact.company or "your company")
        .replace("{title}", contact.title or "your role")
    )


def _str_field(step: JsonObject, key: str) -> str | None:
    """Read a string field from a sequence step (a JSONB object), or None."""
    value = step.get(key)
    return value if isinstance(value, str) else None


def write_message(contact: Contact, step: JsonObject) -> tuple[str, str]:
    """Render a sequence step into a concrete (subject, body) for this contact."""
    subject = _fill(_str_field(step, "subject") or "Quick question", contact)
    body = _fill(
        _str_field(step, "body")
        or "Hi {first_name}, I came across your work at {company} — open to a chat?",
        contact,
    )
    return subject, body


_OPT_OUT = ("not interested", "no thanks", "unsubscribe", "stop", "remove me", "leave me alone")
_INTERESTED = ("interested", "sounds good", "let's talk", "lets talk", "happy to", "tell me more")


def classify_reply(text: str) -> str:
    """Classify an inbound reply: 'opted_out' | 'interested' | 'neutral'."""
    t = text.lower()
    if any(k in t for k in _OPT_OUT):
        return "opted_out"
    if any(k in t for k in _INTERESTED):
        return "interested"
    return "neutral"


def draft_reply(contact: Contact, last_inbound: str | None) -> str:
    """Suggest a reply to the candidate's last message (deterministic stub; Claude slots in)."""
    first = contact.full_name.split()[0] if contact.full_name else "there"
    text = (last_inbound or "").lower()
    if any(k in text for k in ("comp", "salary", "range", "pay")):
        return (
            f"Happy to share, {first}! The range is 120-150k base plus equity, depending on "
            "level. Open to a quick call this week to talk specifics?"
        )
    if any(k in text for k in ("remote", "hybrid", "relocat", "office")):
        return (
            f"Good question, {first} — it's hybrid (2 days/wk) or fully remote within the EU. "
            "Want me to set up a short intro to talk specifics?"
        )
    return (
        f"Thanks for the note, {first}! Would you be open to a quick 20-minute call this week? "
        "Happy to work around your schedule."
    )


def summarize(state: str, last_inbound: str | None) -> str:
    """One-line conversation summary (deterministic stub)."""
    if state == "handed_off":
        return "Interested and a call is scheduled — ready to hand to the hiring team."
    if state == "opted_out":
        return "Politely declined — not looking right now. Conversation closed."
    if state == "awaiting_reply":
        if last_inbound:
            return "They replied with a question; you owe them a response."
        return "You've reached out and are waiting on their reply."
    return "Outreach in progress."


# ---- Claude-backed variants (fall back to the deterministic functions above) ----


def _contact_brief(contact: Contact) -> str:
    return (
        f"name {contact.full_name}, title {contact.title or 'unknown'}, "
        f"company {contact.company or 'unknown'}, location {contact.location or 'unknown'}, "
        f"skills {', '.join(contact.skills or []) or 'unknown'}"
    )


async def draft_message(
    contact: Contact, step: JsonObject, *, brand_voice: str | None = None
) -> tuple[str, str]:
    """Personalized (subject, body) for a step — Claude when enabled, else template fill."""
    subject, body = write_message(contact, step)
    if not llm.is_enabled():
        return subject, body
    channel = _str_field(step, "channel") or "email"
    system = (
        "You are an expert B2B outreach writer (recruiting and sales). Write concise, warm, "
        "specific, non-spammy first-person outreach. No placeholders, no clichés, one clear ask."
    )
    user = (
        f"Recipient: {_contact_brief(contact)}.\n"
        f"Channel: {channel}. Step guidance — subject: {_str_field(step, 'subject') or '(none)'}; "
        f"body angle: {_str_field(step, 'body') or '(none)'}.\n"
        f"Brand voice: {brand_voice or 'professional, friendly, direct'}.\n"
        'Return JSON {"subject": string, "body": string}. Body under 90 words. '
        "For linkedin, subject may be empty."
    )
    obj = await llm.complete_json(system, user, max_tokens=400)
    if obj is not None:
        out_body = obj.get("body")
        if isinstance(out_body, str) and out_body:
            out_subject = obj.get("subject")
            subject_text = out_subject if isinstance(out_subject, str) and out_subject else subject
            return subject_text, out_body
    return subject, body


async def classify_reply_intent(text: str) -> str:
    """'interested' | 'opted_out' | 'neutral' — Claude when enabled, else keyword match."""
    baseline = classify_reply(text)
    if not llm.is_enabled():
        return baseline
    system = "Classify the intent of a reply to a recruiting/sales outreach message."
    user = (
        f"Reply: {text!r}\n"
        'Return JSON {"intent": "interested" | "opted_out" | "neutral"}. '
        "Use opted_out for any decline/unsubscribe, interested for positive engagement."
    )
    obj = await llm.complete_json(system, user, max_tokens=50)
    intent = obj.get("intent") if obj is not None else None
    if isinstance(intent, str) and intent in ("interested", "opted_out", "neutral"):
        return intent
    return baseline


async def draft_reply_text(contact: Contact, last_inbound: str | None) -> str:
    """Suggested reply — Claude when enabled, else the deterministic draft."""
    baseline = draft_reply(contact, last_inbound)
    if not llm.is_enabled() or not last_inbound:
        return baseline
    system = (
        "You write the rep's reply to a candidate/prospect. Warm, concise (2-4 sentences), "
        "address their point, and move toward a short call."
    )
    user = (
        f"Recipient: {_contact_brief(contact)}.\n"
        f"Their last message: {last_inbound!r}\nWrite the reply as plain text."
    )
    return await llm.complete(system, user, max_tokens=250) or baseline


async def rewrite_message(original: str, instruction: str) -> str:
    """One-off rewrite of a message per an instruction (Claude when enabled, else the original)."""
    if not llm.is_enabled() or not original.strip():
        return original
    system = (
        "Rewrite an outreach message per the instruction. Keep the rep's voice, stay concise, "
        "don't fabricate facts. Return only the rewritten message."
    )
    user = f"Original:\n{original}\n\nInstruction: {instruction}\n\nRewritten message:"
    return await llm.complete(system, user, max_tokens=400) or original


_DEFAULT_SEQUENCE: JsonList = [
    {
        "channel": "email",
        "delay_days": 0,
        "subject": "Quick question, {first_name}",
        "body": "Came across your work at {company} — open to a quick chat?",
    },
    {
        "channel": "linkedin",
        "delay_days": 3,
        "subject": "",
        "body": "Following up here, {first_name} — still worth a conversation?",
    },
]


def _coerce_step(s: object) -> JsonObject | None:
    if not isinstance(s, dict):
        return None
    delay = s.get("delay_days")
    return {
        "channel": "linkedin" if str(s.get("channel")) == "linkedin" else "email",
        "delay_days": int(delay)
        if isinstance(delay, int | float) and not isinstance(delay, bool)
        else 0,
        "subject": str(s.get("subject") or ""),
        "body": str(s.get("body") or ""),
    }


async def draft_sequence(objective: str, criteria: JsonObject) -> JsonList:
    """Draft a tailored 2-3 step sequence from the brief (Claude when on, else a default)."""
    if not llm.is_enabled() or not objective.strip():
        return _DEFAULT_SEQUENCE
    system = (
        "You design short B2B outreach sequences (recruiting/sales). 2-3 steps mixing email and "
        "linkedin, escalating gently. Each message under 90 words, first-person, specific, no "
        "clichés. Use {first_name} and {company} placeholders. For linkedin, subject may be empty."
    )
    titles = criteria.get("titles") if isinstance(criteria, dict) else None
    user = (
        f"Objective: {objective}\nAudience titles: {titles}\n"
        'Return JSON {"steps": [{"channel": "email"|"linkedin", "delay_days": int, '
        '"subject": string, "body": string}]}.'
    )
    obj = await llm.complete_json(system, user, max_tokens=800)
    raw = obj.get("steps") if obj else None
    if not isinstance(raw, list):
        return _DEFAULT_SEQUENCE
    steps = [step for step in (_coerce_step(s) for s in raw) if step is not None]
    return steps or _DEFAULT_SEQUENCE


async def summarize_thread(state: str, last_inbound: str | None) -> str:
    """One-line summary — Claude when enabled, else the deterministic summary."""
    baseline = summarize(state, last_inbound)
    if not llm.is_enabled():
        return baseline
    system = "Summarize a recruiting/sales conversation in one short line for the rep."
    user = (
        f"Enrollment state: {state}. Last inbound message: {last_inbound or '(none)'}. "
        "Return one sentence."
    )
    return await llm.complete(system, user, max_tokens=60) or baseline


# --- Service -----------------------------------------------------------------


async def list_drafts(session: AsyncSession, *, workspace_id: str) -> list[Message]:
    """Outbound drafts awaiting human approval (the approval queue)."""
    rows = await session.execute(
        select(Message)
        .where(Message.workspace_id == workspace_id, Message.status == MessageStatus.draft)
        .order_by(Message.created_at)
    )
    return list(rows.scalars().all())


async def approve_message(
    session: AsyncSession, *, workspace_id: str, message_id: str, now: datetime
) -> Message:
    message = await session.get(Message, message_id)
    if message is None or message.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="message not found")
    if message.status != MessageStatus.draft:
        raise HTTPException(status_code=409, detail="message is not a draft")
    message.status = MessageStatus.approved
    # Stamp the idempotency key now (persisted before the send cycle) so a retried send de-dupes.
    if not message.idempotency_key:
        message.idempotency_key = new_id()
    enrollment = await session.get(Enrollment, message.enrollment_id)
    if enrollment is not None and enrollment.state == EnrollmentState.awaiting_approval:
        enrollment.state = EnrollmentState.scheduled
        enrollment.next_run_at = now
    await session.flush()
    return message


async def already_ingested(session: AsyncSession, *, provider_message_id: str | None) -> bool:
    """True if a webhook with this provider message id was already recorded (redelivery dedupe)."""
    if not provider_message_id:
        return False
    seen = (
        (
            await session.execute(
                select(Message.id)
                .where(Message.provider_message_id == provider_message_id)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return seen is not None


async def _apply_inbound(
    session: AsyncSession,
    *,
    enrollment: Enrollment,
    text: str,
    now: datetime,
    channel: Channel = Channel.email,
    provider_message_id: str | None = None,
) -> tuple[Message, str]:
    """Record an inbound reply on an enrollment, classify intent, and transition state."""
    message = Message(
        workspace_id=enrollment.workspace_id,
        enrollment_id=enrollment.id,
        direction=MessageDirection.inbound,
        channel=channel,
        status=MessageStatus.received,
        body=text,
        created_at=now,
        provider_message_id=provider_message_id,
    )
    session.add(message)
    try:
        # Insert in a savepoint so a concurrent redelivery (same provider_message_id) hits the
        # unique index here and is caught, rather than poisoning the whole transaction.
        async with session.begin_nested():
            await session.flush()
    except IntegrityError:
        session.expunge(message)
        return message, "duplicate"

    intent = await classify_reply_intent(text)
    if intent == "interested":
        enrollment.state = EnrollmentState.handed_off
        enrollment.outcome = "interested"
        enrollment.next_run_at = None
    elif intent == "opted_out":
        enrollment.state = EnrollmentState.opted_out
        enrollment.outcome = "opted_out"
        enrollment.next_run_at = None
        contact = await session.get(Contact, enrollment.contact_id)
        ws = await session.get(Workspace, enrollment.workspace_id)
        if contact is not None and contact.email and ws is not None:
            await suppression.suppress(
                session,
                organization_id=ws.organization_id,
                email=contact.email,
                reason=SuppressionReason.opted_out,
                contact_id=contact.id,
            )
    else:
        enrollment.reply_pending = True
    await session.flush()
    return message, intent


async def ingest_reply(
    session: AsyncSession,
    *,
    workspace_id: str,
    enrollment_id: str,
    text: str,
    now: datetime,
    channel: Channel = Channel.email,
    provider_message_id: str | None = None,
) -> tuple[Message, str]:
    """Workspace-scoped reply ingestion (the in-app / authed webhook)."""
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="enrollment not found")
    return await _apply_inbound(
        session,
        enrollment=enrollment,
        text=text,
        now=now,
        channel=channel,
        provider_message_id=provider_message_id,
    )


async def ingest_inbound(
    session: AsyncSession,
    *,
    from_email: str,
    text: str,
    now: datetime,
    enrollment_id: str | None = None,
    channel: Channel = Channel.email,
    provider_message_id: str | None = None,
) -> tuple[Message, str] | None:
    """System inbound (signed provider webhook): thread by enrollment id or sender email."""
    if await already_ingested(session, provider_message_id=provider_message_id):
        return None
    enrollment: Enrollment | None
    if enrollment_id:
        enrollment = await session.get(Enrollment, enrollment_id)
    else:
        enrollment = (
            (
                await session.execute(
                    select(Enrollment)
                    .join(Contact, Enrollment.contact_id == Contact.id)
                    .where(func.lower(Contact.email) == (from_email or "").strip().lower())
                    .order_by(Enrollment.created_at.desc())
                )
            )
            .scalars()
            .first()
        )
    if enrollment is None:
        return None
    return await _apply_inbound(
        session,
        enrollment=enrollment,
        text=text,
        now=now,
        channel=channel,
        provider_message_id=provider_message_id,
    )


async def list_thread(
    session: AsyncSession, *, workspace_id: str, enrollment_id: str
) -> list[Message]:
    rows = await session.execute(
        select(Message)
        .where(
            Message.workspace_id == workspace_id,
            Message.enrollment_id == enrollment_id,
        )
        .order_by(Message.created_at)
    )
    return list(rows.scalars().all())
