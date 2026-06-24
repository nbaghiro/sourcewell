"""Messaging HTTP layer: routes, schemas, serializers (approvals / inbox / webhooks)."""

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.core.config import get_settings
from app.core.crypto import verify_hmac
from app.core.db import get_session
from app.core.types import JsonObject
from app.models import (
    Campaign,
    Channel,
    Contact,
    Enrollment,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.services.insights import audit
from app.services.outreach.messaging import (
    approve_message,
    draft_reply_text,
    ingest_inbound,
    ingest_reply,
    list_thread,
    summarize_thread,
)

router = APIRouter(tags=["messaging"])


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
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
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
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
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
