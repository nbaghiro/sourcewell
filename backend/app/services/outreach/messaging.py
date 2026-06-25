"""Messaging: channels, Writer/Responder agents, and service.

Each agent function has a deterministic baseline (template fill / keyword intent) and an async
Claude-backed variant that falls back to the baseline when the model is unconfigured or errors.
"""

import asyncio
import os
import smtplib
from datetime import datetime
from email.message import EmailMessage

import httpx
from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import llm
from app.core.config import get_settings
from app.core.types import JsonObject
from app.models import (
    Channel,
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


def _send_sync(
    host: str,
    port: int,
    sender: str,
    to: str,
    subject: str,
    body: str,
    unsubscribe_url: str | None,
) -> None:
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = to
    msg["Subject"] = subject
    if unsubscribe_url:
        msg["List-Unsubscribe"] = f"<{unsubscribe_url}>"
        msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    msg.set_content(body)
    with smtplib.SMTP(host, port, timeout=10) as smtp:
        smtp.send_message(msg)


async def send_email(
    *, sender: str, to: str, subject: str, body: str, unsubscribe_url: str | None = None
) -> None:
    if os.getenv("EMAIL_DRY_RUN") == "1":
        return
    s = get_settings()
    await asyncio.to_thread(
        _send_sync, s.smtp_host, s.smtp_port, sender, to, subject, body, unsubscribe_url
    )


async def send_linkedin(*, to_url: str, text: str) -> None:
    """Send a LinkedIn message via Unipile. No-op (dry-run) when Unipile isn't configured."""
    s = get_settings()
    if (
        not (s.unipile_api_key and s.unipile_dsn and s.unipile_account_id)
        or os.getenv("LINKEDIN_DRY_RUN") == "1"
    ):
        return
    async with httpx.AsyncClient(timeout=20.0) as client:
        resp = await client.post(
            f"{s.unipile_dsn.rstrip('/')}/api/v1/chats",
            headers={"X-API-KEY": s.unipile_api_key, "accept": "application/json"},
            json={"account_id": s.unipile_account_id, "text": text, "attendees_ids": [to_url]},
        )
        resp.raise_for_status()


async def send_via_channel(
    *,
    channel: Channel,
    sender: str,
    email: str | None,
    linkedin_url: str | None,
    subject: str,
    body: str,
    unsubscribe_url: str | None = None,
) -> None:
    """Route a send to the right channel. Raises on failure (caller handles retry)."""
    if channel == Channel.linkedin:
        await send_linkedin(to_url=linkedin_url or "", text=body)
    else:
        await send_email(
            sender=sender,
            to=email or "",
            subject=subject,
            body=body,
            unsubscribe_url=unsubscribe_url,
        )


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
    enrollment = await session.get(Enrollment, message.enrollment_id)
    if enrollment is not None and enrollment.state == EnrollmentState.awaiting_approval:
        enrollment.state = EnrollmentState.scheduled
        enrollment.next_run_at = now
    await session.flush()
    return message


async def _apply_inbound(
    session: AsyncSession, *, enrollment: Enrollment, text: str, now: datetime
) -> tuple[Message, str]:
    """Record an inbound reply on an enrollment, classify intent, and transition state."""
    message = Message(
        workspace_id=enrollment.workspace_id,
        enrollment_id=enrollment.id,
        direction=MessageDirection.inbound,
        channel=Channel.email,
        status=MessageStatus.received,
        body=text,
        created_at=now,
    )
    session.add(message)

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
    session: AsyncSession, *, workspace_id: str, enrollment_id: str, text: str, now: datetime
) -> tuple[Message, str]:
    """Workspace-scoped reply ingestion (the in-app / authed webhook)."""
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="enrollment not found")
    return await _apply_inbound(session, enrollment=enrollment, text=text, now=now)


async def ingest_inbound(
    session: AsyncSession,
    *,
    from_email: str,
    text: str,
    now: datetime,
    enrollment_id: str | None = None,
) -> tuple[Message, str] | None:
    """System inbound (signed provider webhook): thread by enrollment id or sender email."""
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
    return await _apply_inbound(session, enrollment=enrollment, text=text, now=now)


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
