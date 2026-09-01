"""Messaging: Writer/Responder agents, and the conversation service.

Each agent function has a deterministic baseline (template fill / keyword intent) and an async
Claude-backed variant that falls back to the baseline when the model is unconfigured or errors.
Delivery itself (seat resolution, transports, thread continuation) lives in this module too —
see `deliver_outbound`.
"""

import asyncio
import re
import smtplib
from dataclasses import dataclass
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
from app.core.logging import logger
from app.core.types import JsonList, JsonObject
from app.ext.unipile import unipile_channel
from app.models import (
    TERMINAL,
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
    SeatType,
    SuppressionReason,
    Workspace,
)
from app.services.sourcing import suppression

# --- Channels (email via SMTP → Mailpit; LinkedIn via Unipile) ---------------
#
# Set EMAIL_DRY_RUN=1 to skip the SMTP call (tests do this). LinkedIn is a no-op unless a Unipile
# key is configured, so multichannel sequences still complete in QA.


class PermanentSendError(Exception):
    """A hard send failure (bad recipient, dead/unreauthed seat) — fail without retrying.

    `recipient_rejected` says whether the *address* was the problem, and it is the only thing that
    may put an address on the suppression list. Most hard failures are ours, not theirs — an
    unreauthed seat, no connected account, an InMail from a seat with no credits — and treating
    those as bounces permanently do-not-contacts a perfectly good candidate over a configuration
    mistake, org-wide and with no way to undo it from the thread.
    """

    def __init__(self, message: str, *, recipient_rejected: bool = False) -> None:
        super().__init__(message)
        self.recipient_rejected = recipient_rejected


class TransientSendError(Exception):
    """A temporary send failure (network / provider 5xx) — retry with backoff."""


_EMAIL_PROVIDERS = (ConnectionProvider.gmail, ConnectionProvider.graph)


def _providers_for(channel: Channel) -> list[ConnectionProvider]:
    return [ConnectionProvider.linkedin] if channel == Channel.linkedin else list(_EMAIL_PROVIDERS)


async def resolve_channel_seat(
    session: AsyncSession,
    *,
    campaign: Campaign | None,
    channel: Channel,
    user_id: str | None = None,
) -> Connection | None:
    """The seat to send from on `channel`: the campaign's designated seat, else a person's own.

    `campaign` is None for a *direct* conversation — a recruiter messaging someone one-to-one,
    with no sequence behind it. There is no designated seat to read then, so the fallback owner is
    `user_id`: the person doing the sending. For a campaign it is the creator, which is what keeps
    a campaign from borrowing a colleague's mailbox.

    Returns None when neither resolves — the caller must fail visibly rather than send from an
    unrelated account. An unhealthy seat is never returned.
    """
    providers = _providers_for(channel)
    if campaign is not None and campaign.seat_id is not None:
        seat = await session.get(Connection, campaign.seat_id)
        if seat is not None and seat.status == ConnectionStatus.ok and seat.provider in providers:
            return seat
    owner_id = campaign.created_by_user_id if campaign is not None else user_id
    if owner_id is None:
        return None
    return (
        (
            await session.execute(
                select(Connection)
                .where(
                    Connection.user_id == owner_id,
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


# The LinkedIn tiers that carry InMail credits. A `basic` (free) seat has none, so an InMail from
# one is rejected by LinkedIn every time.
_INMAIL_SEATS = (SeatType.premium, SeatType.sales_nav, SeatType.recruiter)


def seat_can_inmail(seat: Connection | None) -> bool:
    """Whether this seat could send an InMail at all.

    An unknown seat (None — the single-tenant fallback account) is allowed through: we can't read
    its tier, and refusing would break a deployment that is configured entirely by env.
    """
    return seat is None or seat.seat_type in _INMAIL_SEATS


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


def with_unsubscribe(body: str, unsubscribe_url: str | None) -> str:
    """Append the opt-out line an outbound email has to carry, if there is one to carry.

    `List-Unsubscribe` is what mail clients surface as a button, but we only control the MIME on
    the SMTP path — through a provider we hand over a body, not a message. A line in the body is
    the mechanism that survives every transport, and the one a recipient can actually find. Mail
    sent through Unipile went out with neither: the header was set only by the dev SMTP path, and
    the unsubscribe URL was computed, threaded all the way down here, and dropped on the floor.

    Appended at send time and never written back to `Message.body`: this is transport boilerplate,
    like a signature, and the thread should show what the recruiter actually wrote.
    """
    if not unsubscribe_url:
        return body
    return f"{body}\n\n—\nNot interested? Unsubscribe: {unsubscribe_url}"


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
        # One-click unsubscribe (RFC 8058) — the header a mail client renders as a button. Only
        # this path can set it: through a provider we hand over a body, not a MIME message, so
        # there the opt-out rides in the body instead (`with_unsubscribe`).
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
    inmail: bool = False,
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
    # `unipile_account_id` is the deployment's connected *LinkedIn* account, so it is only a
    # fallback for LinkedIn. Applying it to email too meant an org with no mailbox connected
    # posted to the provider's /emails with a LinkedIn account id: the provider answered 4xx, the
    # send was classified as a hard failure, and the recipient's perfectly good address was
    # suppressed as a bounce. With no email seat there is simply no provider account — email
    # falls through to the SMTP path below, which is what it is there for.
    account_id = (seat.external_id if seat else None) or (
        s.unipile_account_id or None if channel == Channel.linkedin else None
    )
    message.account_id = account_id
    if not message.idempotency_key:
        message.idempotency_key = new_id()
    provider = unipile_channel(channel.value)
    thread_ref = (
        await _last_thread_ref(session, enrollment_id=message.enrollment_id) if reply else None
    )

    if channel == Channel.linkedin:
        # An InMail only ever opens a conversation. Continuing one is an ordinary message in a chat
        # that already exists, so it must not be recorded (or billed) as an InMail. Stamped here
        # rather than by the caller, because only the transport knows which path it took.
        message.is_inmail = inmail and not (reply and thread_ref)
        if s.linkedin_dry_run:
            return  # dev/demo: LinkedIn is simulated offline — nothing to send
        if provider is None or not account_id:
            # No connected LinkedIn account: a real failure, not a silent "sent". The touchpoint
            # path routes around this via email fallback; reply paths surface it to the caller.
            raise PermanentSendError("no LinkedIn account connected")
        if seat is not None and seat.status != ConnectionStatus.ok:
            raise PermanentSendError("LinkedIn seat needs reauthentication")
        if message.is_inmail and not seat_can_inmail(seat):
            # LinkedIn would reject this outright: InMail spends credits only a paid seat has.
            # Say which fix is needed, instead of letting the provider return a bare 4xx that
            # surfaces as the misleading "recipient unreachable".
            raise PermanentSendError(
                "this LinkedIn seat has no InMail credits — it needs Premium, Sales Navigator or "
                "Recruiter, or turn InMail off on the campaign"
            )
        try:
            if reply and thread_ref:
                if not await provider.reply(
                    account_id=account_id,
                    thread_id=thread_ref,
                    body=message.body,
                    idempotency_key=message.idempotency_key,
                ):
                    raise PermanentSendError("LinkedIn rejected this reply — the chat is gone")
                message.external_id = thread_ref
            else:
                chat_id = await provider.send(
                    account_id=account_id,
                    to=target,
                    subject=message.subject,
                    body=message.body,
                    # InMail is the campaign's opt-in, never the default: it needs credits the
                    # seat may not have (a free/basic account has none, and the send fails), and
                    # it bills at twice a DM. `use_inmail` on the campaign decides.
                    inmail=inmail,
                    idempotency_key=message.idempotency_key,
                )
                if chat_id is None:
                    raise PermanentSendError(
                        "LinkedIn recipient unreachable", recipient_rejected=True
                    )
                message.external_id = chat_id
        except PermanentSendError:
            raise
        except Exception as exc:
            raise TransientSendError(str(exc)) from exc
        return

    # Email: real ESP via Unipile when configured, else SMTP (dev/Mailpit) with threading headers.
    # Both carry the same body — the opt-out line is added here, above the transport split, so it
    # can't go missing from one of them again.
    body = with_unsubscribe(message.body, unsubscribe_url)
    if provider is not None and account_id and not s.email_dry_run:
        if seat is not None and seat.status != ConnectionStatus.ok:
            raise PermanentSendError("email seat needs reauthentication")
        try:
            if reply and thread_ref:
                if not await provider.reply(
                    account_id=account_id,
                    thread_id=thread_ref,
                    body=body,
                    idempotency_key=message.idempotency_key,
                ):
                    raise PermanentSendError("the email provider rejected this reply")
                message.external_id = thread_ref
            else:
                mid = await provider.send(
                    account_id=account_id,
                    to=target,
                    subject=message.subject,
                    body=body,
                    idempotency_key=message.idempotency_key,
                )
                if mid is None:
                    # None means the provider *refused* it — a transient failure raises out of
                    # `_permanent` instead. An accepted send with no id to thread on comes back as
                    # "", which is not a failure: the mail left, we just can't chain a reply to it.
                    raise PermanentSendError(
                        "the email provider rejected this message", recipient_rejected=True
                    )
                message.external_id = mid or None
        except PermanentSendError:
            raise
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
                body,
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


# Phrases that mean "don't contact me" wherever they appear. Matched on word boundaries, not as
# raw substrings: an opt-out permanently suppresses the address and the recruiter cannot undo it
# from the thread, so a false positive silently costs a real candidate.
_OPT_OUT = ("not interested", "no thanks", "remove me", "leave me alone", "unsubscribe")
# "stop" only counts as the *whole* message — the SMS convention. As a substring it fired on
# "I'll stop by Thursday" and "stopped by your careers page", which are the opposite of an opt-out.
_OPT_OUT_ALONE = ("stop",)
_INTERESTED = ("interested", "sounds good", "let's talk", "lets talk", "happy to", "tell me more")


def _mentions(text: str, phrases: tuple[str, ...]) -> bool:
    return any(re.search(rf"\b{re.escape(p)}\b", text) for p in phrases)


def classify_reply(text: str) -> str:
    """Classify an inbound reply: 'opted_out' | 'interested' | 'neutral'."""
    t = text.lower()
    if t.strip(" \t\r\n.!?") in _OPT_OUT_ALONE:
        return "opted_out"
    if _mentions(t, _OPT_OUT):
        return "opted_out"
    if _mentions(t, _INTERESTED):
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


@dataclass(frozen=True)
class ChannelAvailability:
    """Whether one channel can carry a message to this contact right now, and why not."""

    channel: Channel
    available: bool
    target: str | None
    reason: str | None = None


async def channel_availability(
    session: AsyncSession,
    *,
    campaign: Campaign | None,
    contact: Contact,
    user_id: str | None = None,
) -> list[ChannelAvailability]:
    """Which channels can reach this contact — what the composer offers as a send option.

    A channel needs a destination on the contact; LinkedIn additionally needs a seat we can
    actually send from (there is no fallback transport for it the way SMTP backs email). The two
    LinkedIn reasons are kept apart because they need different fixes: a missing profile is a data
    problem, a missing seat is a setup one.

    `campaign` is None on a direct conversation; the seat then comes from `user_id`.
    """
    email_ok = bool(contact.email)
    seat = await resolve_channel_seat(
        session, campaign=campaign, channel=Channel.linkedin, user_id=user_id
    )
    if not contact.linkedin_url:
        li_reason: str | None = "no LinkedIn profile on this contact"
    elif not linkedin_transport_ready(seat):
        li_reason = "no LinkedIn account connected"
    else:
        li_reason = None
    return [
        ChannelAvailability(
            channel=Channel.email,
            available=email_ok,
            target=contact.email,
            reason=None if email_ok else "no email address on this contact",
        ),
        ChannelAvailability(
            channel=Channel.linkedin,
            available=li_reason is None,
            target=contact.linkedin_url,
            reason=li_reason,
        ),
    ]


async def resolve_channel(
    session: AsyncSession,
    *,
    campaign: Campaign | None,
    enrollment_id: str,
    contact: Contact,
    user_id: str | None = None,
) -> Channel:
    """The channel a reply defaults to: the one the thread is already on, if it still works.

    A thread that started on LinkedIn stays on LinkedIn — unless that channel can't carry a message
    to this contact (no profile, no connected seat), in which case we fall back to the other one.
    """
    options = {
        opt.channel: opt
        for opt in await channel_availability(
            session, campaign=campaign, contact=contact, user_id=user_id
        )
    }
    last = (
        (
            await session.execute(
                select(Message.channel)
                .where(Message.enrollment_id == enrollment_id)
                .order_by(Message.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if last is not None and options[last].available:
        return last
    for candidate in (Channel.email, Channel.linkedin):
        if options[candidate].available:
            return candidate
    return last or Channel.email


async def send_conversation_message(
    session: AsyncSession,
    *,
    workspace_id: str,
    enrollment: Enrollment,
    campaign: Campaign | None,
    contact: Contact,
    channel: Channel,
    subject: str | None,
    body: str,
    sender: str,
    organization_id: str | None,
    now: datetime,
    origin: str = "human",
    user_id: str | None = None,
) -> Message:
    """Deliver one outbound message in a live conversation, then record it on the thread.

    The single path for a *human or agent* reply (the sequence's own touchpoints go through the
    enrollment state machine). Delivery happens first: a `Message` is only written as `sent` once
    the provider accepted it, so the thread never shows a message that never left.

    `campaign` is None on a direct conversation — no sequence, no InMail setting to inherit, and
    the seat comes from `user_id` (the person sending) instead of the campaign's creator.

    LinkedIn sends otherwise inherit the campaign's InMail setting, so a manual reply and a
    touchpoint on the same campaign go out the same way.
    """
    text = body.strip()
    if not text:
        raise HTTPException(status_code=422, detail="message body is empty")
    # Checked on every channel, not just email. Suppression is keyed on an address, but it records
    # that this *person* asked not to be contacted — including via the agent's own `opt_out` tool.
    # Gating it on `channel == email` let the composer and the agent keep messaging an opted-out
    # candidate on LinkedIn, while the sequence's own touchpoints (which check unconditionally)
    # correctly refused. One of the two was wrong; this is the one that was.
    if organization_id and contact.email:
        if await suppression.is_suppressed(
            session,
            organization_id=organization_id,
            email=contact.email,
            workspace_id=workspace_id,
        ):
            raise HTTPException(status_code=409, detail="this contact has opted out")

    unsub = (
        suppression.unsubscribe_url(organization_id, contact.email)
        if organization_id and contact.email and channel == Channel.email
        else None
    )
    seat = await resolve_channel_seat(session, campaign=campaign, channel=channel, user_id=user_id)
    # Built first so the transport can stamp the provider thread id and idempotency key onto it,
    # but only added to the session once the send succeeded: the thread must never show a message
    # that never left.
    message = Message(
        workspace_id=workspace_id,
        enrollment_id=enrollment.id,
        direction=MessageDirection.outbound,
        channel=channel,
        status=MessageStatus.sent,
        # LinkedIn carries no subject: the transport drops it, so storing one would show a subject
        # line in the thread that was never sent.
        subject=(subject or None) if channel == Channel.email else None,
        body=text,
        sent_at=now,
        created_at=now,
        origin=origin,
    )
    try:
        await deliver_outbound(
            session,
            message=message,
            contact=contact,
            seat=seat,
            sender=sender,
            unsubscribe_url=unsub,
            reply=True,
            # A direct conversation has no campaign to opt in, so it is never an InMail.
            inmail=bool(campaign is not None and campaign.use_inmail),
        )
    except PermanentSendError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except TransientSendError as exc:
        raise HTTPException(status_code=502, detail=f"send failed: {exc}") from exc

    session.add(message)
    enrollment.reply_pending = False
    # Flushed, not committed: this runs inside a savepoint on the worker's agent path, and the
    # transaction boundary is the caller's to own. The HTTP caller commits immediately after this
    # returns — see `api/messaging.send_reply` — so a failure in the audit write that follows
    # can't discard the record of a message the candidate has already received.
    await session.flush()
    return message


async def open_direct_conversation(
    session: AsyncSession, *, workspace_id: str, contact: Contact
) -> Enrollment:
    """The thread for messaging this contact one-to-one — reusing one if it already exists.

    Find-or-create, so "Message" is idempotent: clicking it twice lands in the same conversation
    rather than forking a second thread with the same person. An existing *campaign* thread wins
    over making a direct one — the recruiter means "talk to this person", and splitting that
    across two threads is how half a conversation goes missing.

    The row is created before anything is sent, and an enrollment with no messages never appears
    in the inbox list (that list is built from messages), so opening a conversation and walking
    away leaves nothing behind to clean up.
    """
    existing = (
        (
            await session.execute(
                select(Enrollment)
                .where(
                    Enrollment.workspace_id == workspace_id,
                    Enrollment.contact_id == contact.id,
                    Enrollment.state.not_in(TERMINAL),
                )
                .order_by(Enrollment.campaign_id.is_(None), Enrollment.created_at.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    enrollment = Enrollment(
        workspace_id=workspace_id,
        campaign_id=None,  # direct: no sequence, so the worker never ticks it
        contact_id=contact.id,
        state=EnrollmentState.active,
    )
    session.add(enrollment)
    await session.flush()
    return enrollment


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


# --- Inbound: record, then route ---------------------------------------------
#
# The two halves are deliberately separate. *Recording* is cheap and must happen inside the
# provider webhook so the reply is durable before we acknowledge it. *Routing* — classify, move
# the enrollment, possibly run the Outreach agent — is slow (LLM calls) and runs on the worker.
# Splitting them is also what makes the receiver idempotent: `provider_message_id` is checked at
# record time, so a webhook redelivery never reaches the agent a second time.


async def already_ingested(
    session: AsyncSession, *, provider_message_id: str | None, workspace_id: str
) -> bool:
    """True if this provider message id was already recorded *in this workspace*.

    Scoped, not global: provider ids are not unique across accounts, so a global check made one
    workspace's recorded id shadow every other workspace's identical id — dropping a real reply as
    a "redelivery".
    """
    if not provider_message_id:
        return False
    seen = (
        (
            await session.execute(
                select(Message.id)
                .where(
                    Message.workspace_id == workspace_id,
                    Message.provider_message_id == provider_message_id,
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return seen is not None


async def record_inbound(
    session: AsyncSession,
    *,
    enrollment: Enrollment,
    text: str,
    now: datetime,
    channel: Channel = Channel.email,
    provider_message_id: str | None = None,
    external_id: str | None = None,
    routed: bool = False,
) -> Message | None:
    """Write an inbound message onto the thread.

    Returns None when `provider_message_id` is already recorded *in this workspace* — the provider
    redelivered an event we've handled, and re-recording it would both duplicate the bubble and
    re-trigger the agent (which at full autonomy means a second real message to the candidate).

    `routed=True` marks it handled on arrival, for the synchronous in-app path.
    """
    if await already_ingested(
        session,
        provider_message_id=provider_message_id,
        workspace_id=enrollment.workspace_id,
    ):
        return None
    message = Message(
        workspace_id=enrollment.workspace_id,
        enrollment_id=enrollment.id,
        direction=MessageDirection.inbound,
        channel=channel,
        status=MessageStatus.received,
        body=text,
        created_at=now,
        external_id=external_id,
        provider_message_id=provider_message_id,
        processed_at=now if routed else None,
    )
    try:
        # A savepoint, not the outer transaction: two redeliveries can race past the check above
        # and only the losing INSERT should unwind, leaving the caller's work intact. The unique
        # index — not just the check — is what makes this safe under concurrency.
        async with session.begin_nested():
            session.add(message)
            await session.flush()
    except IntegrityError:
        session.expunge(message)
        return None
    return message


async def pending_inbound(session: AsyncSession, *, limit: int = 50) -> list[Message]:
    """Inbound messages a provider webhook parked for the worker to route, oldest first.

    Claimed with `FOR UPDATE SKIP LOCKED`, like the other two due-queries in the worker. Without
    it two worker processes polling the same 10-second window both picked up the same reply and
    both ran the Outreach agent on it — which at full autonomy is a second real message to the
    candidate, the exact failure the record/route split exists to prevent.
    """
    rows = await session.execute(
        select(Message)
        .where(
            Message.direction == MessageDirection.inbound,
            Message.processed_at.is_(None),
        )
        .order_by(Message.created_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    return list(rows.scalars().all())


async def route_inbound(
    session: AsyncSession, *, enrollment: Enrollment, message: Message, now: datetime
) -> str:
    """Classify a recorded reply and transition the enrollment. Returns the intent."""
    text = message.body
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
    message.processed_at = now
    await session.flush()
    return intent


async def resolve_inbound_enrollment(
    session: AsyncSession, *, from_email: str, enrollment_id: str | None = None
) -> Enrollment | None:
    """Thread a provider event to an enrollment by explicit id, else by the sender's address.

    The address lookup has no tenant to anchor on, so when the same address is enrolled in more
    than one workspace it refuses to choose: attaching a candidate's reply to the wrong customer's
    thread is worse than not recording it. Senders that resolve to a single workspace — the
    ordinary case — are unaffected.
    """
    if enrollment_id:
        return await session.get(Enrollment, enrollment_id)
    address = (from_email or "").strip().lower()
    if not address:
        return None
    candidates = list(
        (
            await session.execute(
                select(Enrollment)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .where(func.lower(Contact.email) == address)
                .order_by(Enrollment.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    if not candidates:
        return None
    if len({e.workspace_id for e in candidates}) > 1:
        logger.warning(
            "inbound: sender %s is enrolled in %d workspaces — dropping, nothing to scope by",
            address,
            len({e.workspace_id for e in candidates}),
        )
        return None
    return candidates[0]


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
