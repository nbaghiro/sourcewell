"""Messaging: channels, Writer/Responder agents, service, and endpoints (all merged).

Each agent function has a deterministic baseline (template fill / keyword intent) and an async
Claude-backed variant that falls back to the baseline when the model is unconfigured or errors.
"""

import asyncio
import json
import os
import smtplib
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Annotated

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import llm
from app.core.config import get_settings
from app.core.db import get_session
from app.core.signing import verify_hmac
from app.core.types import JsonObject
from app.insights import audit
from app.models import (
    Campaign,
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
from app.people import suppression
from app.workspace.tenancy import ContextDep, SessionDep, require_workspace

router = APIRouter(tags=["messaging"])


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


# --- Schemas -----------------------------------------------------------------


class ReplyRequest(BaseModel):
    enrollment_id: str
    text: str


class SendRequest(BaseModel):
    text: str


class MessageOut(BaseModel):
    id: str
    enrollment_id: str
    direction: str
    channel: str
    status: str
    subject: str | None
    body: str
    sent_at: str | None
    scheduled_at: str | None
    created_at: str | None


def dump_message(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        enrollment_id=m.enrollment_id,
        direction=m.direction.value,
        channel=m.channel.value,
        status=m.status.value,
        subject=m.subject,
        body=m.body,
        sent_at=m.sent_at.isoformat() if m.sent_at else None,
        scheduled_at=m.scheduled_at.isoformat() if m.scheduled_at else None,
        created_at=m.created_at.isoformat() if m.created_at else None,
    )


class ApprovalOut(MessageOut):
    contact_name: str
    contact_title: str | None
    contact_company: str | None
    contact_avatar: str | None
    score: int
    step: int


class InboxItemOut(BaseModel):
    enrollment_id: str
    contact_name: str | None
    contact_title: str | None
    contact_company: str | None
    contact_avatar: str | None
    state: str | None
    outcome: str | None
    channel: str
    message_count: int
    unread: bool
    last_at: str | None
    last_message: MessageOut


class ConvEnrollment(BaseModel):
    id: str
    state: str
    score: int
    current_step: int
    outcome: str | None


class ConvContact(BaseModel):
    id: str | None
    name: str | None
    title: str | None
    company: str | None
    location: str | None
    email: str | None
    linkedin_url: str | None
    avatar_url: str | None
    skills: list[str]


class ConvCampaign(BaseModel):
    id: str | None
    name: str | None
    steps: int


class ConversationOut(BaseModel):
    enrollment: ConvEnrollment
    contact: ConvContact
    campaign: ConvCampaign
    channel: str
    messages: list[MessageOut]


class DraftOut(BaseModel):
    text: str


class SummaryOut(BaseModel):
    summary: str


class StatusIdOut(BaseModel):
    status: str
    id: str


class ReplyWebhookOut(BaseModel):
    intent: str
    message: MessageOut


# --- Endpoints ---------------------------------------------------------------


@router.get("/approvals", response_model=list[ApprovalOut])
async def list_approvals(ctx: ContextDep, session: SessionDep) -> list[ApprovalOut]:
    ws = require_workspace(ctx)
    rows = (
        (
            await session.execute(
                select(Message, Enrollment, Contact)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .where(Message.workspace_id == ws, Message.status == MessageStatus.draft)
                .order_by(Enrollment.score.desc())
            )
        )
        .tuples()
        .all()
    )
    return [
        ApprovalOut(
            **dump_message(m).model_dump(),
            contact_name=c.full_name,
            contact_title=c.title,
            contact_company=c.company,
            contact_avatar=c.avatar_url,
            score=e.score,
            step=e.current_step,
        )
        for m, e, c in rows
    ]


@router.post("/messages/{message_id}/approve", response_model=MessageOut)
async def approve_message_endpoint(
    message_id: str, ctx: ContextDep, session: SessionDep
) -> MessageOut:
    ws = require_workspace(ctx)
    message = await approve_message(
        session, workspace_id=ws, message_id=message_id, now=datetime.now(UTC)
    )
    await audit.record(
        session,
        ctx,
        action="message.approved",
        summary="Approved a drafted message",
        target_type="message",
        target_id=message_id,
    )
    return dump_message(message)


async def _owned_enrollment(session: SessionDep, ws: str, enrollment_id: str) -> Enrollment:
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id != ws:
        raise HTTPException(status_code=404, detail="conversation not found")
    return enrollment


def _last_inbound(messages: list[Message]) -> str | None:
    return next(
        (m.body for m in reversed(messages) if m.direction == MessageDirection.inbound), None
    )


class EditMessageRequest(BaseModel):
    subject: str | None = None
    body: str | None = None


@router.patch("/messages/{message_id}", response_model=MessageOut)
async def edit_message(
    message_id: str, body: EditMessageRequest, ctx: ContextDep, session: SessionDep
) -> MessageOut:
    """Edit a draft before it's approved/sent."""
    ws = require_workspace(ctx)
    message = await session.get(Message, message_id)
    if message is None or message.workspace_id != ws:
        raise HTTPException(status_code=404, detail="message not found")
    if message.status != MessageStatus.draft:
        raise HTTPException(status_code=409, detail="only drafts can be edited")
    if body.subject is not None:
        message.subject = body.subject
    if body.body is not None:
        message.body = body.body
    await session.flush()
    return dump_message(message)


@router.get("/inbox", response_model=list[InboxItemOut])
async def inbox(ctx: ContextDep, session: SessionDep) -> list[InboxItemOut]:
    ws = require_workspace(ctx)
    rows = await session.execute(
        select(Message).where(Message.workspace_id == ws).order_by(Message.created_at)
    )
    by_enrollment: dict[str, list[Message]] = {}
    for m in rows.scalars().all():
        by_enrollment.setdefault(m.enrollment_id, []).append(m)

    items: list[InboxItemOut] = []
    for enrollment_id, messages in by_enrollment.items():
        enrollment = await session.get(Enrollment, enrollment_id)
        contact = await session.get(Contact, enrollment.contact_id) if enrollment else None
        last = messages[-1]
        has_unread = last.direction == MessageDirection.inbound and (
            enrollment is None
            or enrollment.last_read_at is None
            or (last.created_at is not None and last.created_at > enrollment.last_read_at)
        )
        items.append(
            InboxItemOut(
                enrollment_id=enrollment_id,
                contact_name=contact.full_name if contact else None,
                contact_title=contact.title if contact else None,
                contact_company=contact.company if contact else None,
                contact_avatar=contact.avatar_url if contact else None,
                state=enrollment.state.value if enrollment else None,
                outcome=enrollment.outcome if enrollment else None,
                channel=messages[0].channel.value,  # the channel the outreach started on
                message_count=len(messages),
                unread=has_unread,
                last_at=last.created_at.isoformat() if last.created_at else None,
                last_message=dump_message(last),
            )
        )
    items.sort(key=lambda it: it.last_at or "", reverse=True)
    return items


@router.get("/inbox/{enrollment_id}", response_model=ConversationOut)
async def conversation(enrollment_id: str, ctx: ContextDep, session: SessionDep) -> ConversationOut:
    """Full conversation for the messenger: contact profile, campaign, state, channel, messages."""
    ws = require_workspace(ctx)
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id != ws:
        raise HTTPException(status_code=404, detail="conversation not found")
    contact = await session.get(Contact, enrollment.contact_id)
    campaign = await session.get(Campaign, enrollment.campaign_id)
    messages = await list_thread(session, workspace_id=ws, enrollment_id=enrollment_id)
    # Primary channel = the channel of the most recent message.
    channel = messages[-1].channel.value if messages else "email"
    return ConversationOut(
        enrollment=ConvEnrollment(
            id=enrollment.id,
            state=enrollment.state.value,
            score=enrollment.score,
            current_step=enrollment.current_step,
            outcome=enrollment.outcome,
        ),
        contact=ConvContact(
            id=contact.id if contact else None,
            name=contact.full_name if contact else None,
            title=contact.title if contact else None,
            company=contact.company if contact else None,
            location=contact.location if contact else None,
            email=contact.email if contact else None,
            linkedin_url=contact.linkedin_url if contact else None,
            avatar_url=contact.avatar_url if contact else None,
            skills=contact.skills if contact else [],
        ),
        campaign=ConvCampaign(
            id=campaign.id if campaign else None,
            name=campaign.name if campaign else None,
            steps=len(campaign.sequence) if campaign else 0,
        ),
        channel=channel,
        messages=[dump_message(m) for m in messages],
    )


@router.post("/inbox/{enrollment_id}/reply", response_model=MessageOut)
async def send_reply(
    enrollment_id: str, body: SendRequest, ctx: ContextDep, session: SessionDep
) -> MessageOut:
    """Send a manual outbound reply from the recruiter in this conversation."""
    ws = require_workspace(ctx)
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id != ws:
        raise HTTPException(status_code=404, detail="conversation not found")
    last = await list_thread(session, workspace_id=ws, enrollment_id=enrollment_id)
    channel = last[-1].channel if last else Channel.email
    now = datetime.now(UTC)
    message = Message(
        workspace_id=ws,
        enrollment_id=enrollment_id,
        direction=MessageDirection.outbound,
        channel=channel,
        status=MessageStatus.sent,
        body=body.text,
        sent_at=now,
        created_at=now,
    )
    session.add(message)
    enrollment.reply_pending = False
    await session.flush()
    await audit.record(
        session,
        ctx,
        action="reply.sent",
        summary="Sent a manual reply",
        target_type="enrollment",
        target_id=enrollment_id,
    )
    return dump_message(message)


@router.post("/inbox/{enrollment_id}/draft", response_model=DraftOut)
async def draft_reply_endpoint(
    enrollment_id: str, ctx: ContextDep, session: SessionDep
) -> DraftOut:
    """AI-suggested reply for this conversation (Writer stub; Claude slots in here)."""
    ws = require_workspace(ctx)
    enrollment = await _owned_enrollment(session, ws, enrollment_id)
    contact = await session.get(Contact, enrollment.contact_id)
    messages = await list_thread(session, workspace_id=ws, enrollment_id=enrollment_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    return DraftOut(text=await draft_reply_text(contact, _last_inbound(messages)))


@router.get("/inbox/{enrollment_id}/summary", response_model=SummaryOut)
async def conversation_summary(
    enrollment_id: str, ctx: ContextDep, session: SessionDep
) -> SummaryOut:
    """One-line conversation summary (Writer stub)."""
    ws = require_workspace(ctx)
    enrollment = await _owned_enrollment(session, ws, enrollment_id)
    messages = await list_thread(session, workspace_id=ws, enrollment_id=enrollment_id)
    summary = await summarize_thread(enrollment.state.value, _last_inbound(messages))
    return SummaryOut(summary=summary)


@router.post("/inbox/{enrollment_id}/read", response_model=StatusIdOut)
async def mark_read(enrollment_id: str, ctx: ContextDep, session: SessionDep) -> StatusIdOut:
    ws = require_workspace(ctx)
    enrollment = await _owned_enrollment(session, ws, enrollment_id)
    enrollment.last_read_at = datetime.now(UTC)
    await session.flush()
    return StatusIdOut(status="read", id=enrollment_id)


@router.get("/enrollments/{enrollment_id}/messages", response_model=list[MessageOut])
async def thread(enrollment_id: str, ctx: ContextDep, session: SessionDep) -> list[MessageOut]:
    ws = require_workspace(ctx)
    messages = await list_thread(session, workspace_id=ws, enrollment_id=enrollment_id)
    return [dump_message(m) for m in messages]


@router.post("/webhooks/reply", response_model=ReplyWebhookOut)
async def reply_webhook(
    body: ReplyRequest, ctx: ContextDep, session: SessionDep
) -> ReplyWebhookOut:
    """In-app / authed reply (used by the inbox 'simulate reply' and QA)."""
    ws = require_workspace(ctx)
    message, intent = await ingest_reply(
        session,
        workspace_id=ws,
        enrollment_id=body.enrollment_id,
        text=body.text,
        now=datetime.now(UTC),
    )
    return ReplyWebhookOut(intent=intent, message=dump_message(message))


class InboundWebhookOut(BaseModel):
    status: str
    intent: str | None


@router.post("/webhooks/inbound", response_model=InboundWebhookOut)
async def inbound_webhook(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> InboundWebhookOut:
    """System inbound from an email provider (HMAC-signed, no user session).

    Threads to an enrollment by `enrollment_id` or by the sender's email. Payload (JSON):
    `{"from": str, "text": str, "enrollment_id"?: str}`, signed in the `X-Signature` header.
    """
    secret = get_settings().inbound_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="inbound webhook not configured")
    raw = await request.body()
    if not verify_hmac(raw, request.headers.get("X-Signature"), secret=secret):
        raise HTTPException(status_code=401, detail="invalid signature")
    try:
        parsed: object = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON") from None
    payload: JsonObject = parsed if isinstance(parsed, dict) else {}

    def _str(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    result = await ingest_inbound(
        session,
        from_email=_str("from") or _str("from_email") or "",
        text=_str("text") or _str("body") or "",
        now=datetime.now(UTC),
        enrollment_id=_str("enrollment_id"),
    )
    if result is None:
        return InboundWebhookOut(status="ignored", intent=None)
    return InboundWebhookOut(status="ingested", intent=result[1])
