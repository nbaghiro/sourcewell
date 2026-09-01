"""Messaging HTTP layer: routes, schemas, serializers (approvals / inbox / webhooks)."""

import hmac
import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import case, delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.core.config import get_settings
from app.core.crypto import verify_hmac
from app.core.db import get_session
from app.core.logging import logger
from app.core.types import JsonObject
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
from app.services.insights import audit
from app.services.outreach import receiving
from app.services.outreach.enrollment import tick
from app.services.outreach.messaging import (
    approve_message,
    channel_availability,
    draft_reply_text,
    list_thread,
    open_direct_conversation,
    record_inbound,
    resolve_channel,
    resolve_inbound_enrollment,
    send_conversation_message,
    summarize_thread,
)

router = APIRouter(tags=["messaging"])

# Reject a signed inbound webhook whose timestamp is older than this (replay window).
_WEBHOOK_MAX_SKEW_SECONDS = 300


# --- Schemas -----------------------------------------------------------------


class SendRequest(BaseModel):
    text: str
    # Which channel to send on. Omitted = whichever the thread is already on (see resolve_channel).
    channel: Channel | None = None
    # Email only; ignored on LinkedIn, which has no subject line.
    subject: str | None = None
    origin: str = "human"  # "human" (typed) or "ai" (sending an AI suggestion as-is)


class MessageOut(BaseModel):
    id: str
    enrollment_id: str
    direction: MessageDirection
    channel: Channel
    status: MessageStatus
    subject: str | None
    body: str
    sent_at: str | None
    scheduled_at: str | None
    created_at: str | None
    origin: str


def dump_message(m: Message) -> MessageOut:
    return MessageOut(
        id=m.id,
        enrollment_id=m.enrollment_id,
        direction=m.direction,
        channel=m.channel,
        status=m.status,
        subject=m.subject,
        body=m.body,
        sent_at=m.sent_at.isoformat() if m.sent_at else None,
        scheduled_at=m.scheduled_at.isoformat() if m.scheduled_at else None,
        created_at=m.created_at.isoformat() if m.created_at else None,
        origin=m.origin,
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
    state: EnrollmentState | None
    outcome: str | None
    # They answered and it wasn't a clear yes or no, so the ball is with the recruiter. Its own
    # flag rather than a state because the enrollment is still mid-sequence: `state` stays
    # `awaiting_reply` (what the next touchpoint is gated on), which alone reads as "waiting
    # on them" long after they've written back.
    reply_pending: bool
    channel: Channel
    message_count: int
    unread: bool
    last_at: str | None
    last_message: MessageOut


class ConvEnrollment(BaseModel):
    id: str
    state: EnrollmentState
    score: int
    current_step: int
    outcome: str | None
    # See `InboxItemOut.reply_pending` — the recruiter owes this conversation an answer.
    reply_pending: bool


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
    channel: Channel
    messages: list[MessageOut]


class DraftOut(BaseModel):
    text: str


class SummaryOut(BaseModel):
    summary: str


class StatusIdOut(BaseModel):
    status: str
    id: str


class ChannelOptionOut(BaseModel):
    """One send option for the composer: can we reach this contact here, and if not why not."""

    channel: str
    available: bool
    target: str | None
    reason: str | None


class ChannelsOut(BaseModel):
    default: str
    options: list[ChannelOptionOut]


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
    now = datetime.now(UTC)
    message = await approve_message(session, workspace_id=ws, message_id=message_id, now=now)
    # Approving *is* the send: drive the state machine now instead of leaving the message queued
    # until the next worker poll, so the UI can report what actually happened. `tick` sends the
    # approved message — this very row — and stamps its status, which is what we return. The
    # governor can still defer it (cap / sending window): it then stays `approved` and the worker
    # picks it up at retry time.
    enrollment = await session.get(Enrollment, message.enrollment_id)
    if enrollment is not None and enrollment.state == EnrollmentState.scheduled:
        await tick(session, enrollment=enrollment, now=now)
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


# How many threads one page of the inbox carries.
_INBOX_PAGE = 100


@router.get("/inbox", response_model=list[InboxItemOut])
async def inbox(
    ctx: ContextDep,
    session: SessionDep,
    limit: int = Query(_INBOX_PAGE, ge=1, le=200),
    offset: int = Query(0, ge=0),
) -> list[InboxItemOut]:
    """One row per thread, newest first.

    The per-thread aggregates are computed in the database. This used to select *every* message
    row in the workspace with no limit and group them in Python, keeping only the last of each
    thread plus a count — so rendering ten rows pulled a year of message bodies into memory.
    """
    ws = require_workspace(ctx)
    # Rank each thread's messages by recency so the newest and the oldest can be picked out
    # without carrying the ones in between.
    newest = func.row_number().over(
        partition_by=Message.enrollment_id, order_by=Message.created_at.desc()
    )
    oldest = func.row_number().over(
        partition_by=Message.enrollment_id, order_by=Message.created_at.asc()
    )
    ranked = (
        select(
            Message.id.label("message_id"),
            Message.enrollment_id.label("enrollment_id"),
            Message.channel.label("channel"),
            newest.label("newest"),
            oldest.label("oldest"),
            # Windowed over the whole thread, before the outer query narrows to its two ends.
            func.count().over(partition_by=Message.enrollment_id).label("message_count"),
            func.max(Message.created_at).over(partition_by=Message.enrollment_id).label("last_at"),
        )
        .where(Message.workspace_id == ws)
        .subquery()
    )
    # Only a thread's two ends survive: the newest message (what the row previews) and the oldest
    # (the channel the outreach started on). A one-message thread is both, and collapses to it.
    threads = (
        select(
            ranked.c.enrollment_id,
            func.max(ranked.c.message_count).label("message_count"),
            func.max(ranked.c.last_at).label("last_at"),
            func.max(case((ranked.c.newest == 1, ranked.c.message_id))).label("last_message_id"),
            func.max(case((ranked.c.oldest == 1, ranked.c.channel))).label("first_channel"),
        )
        .where(or_(ranked.c.newest == 1, ranked.c.oldest == 1))
        .group_by(ranked.c.enrollment_id)
        .order_by(func.max(ranked.c.last_at).desc())
        .limit(limit)
        .offset(offset)
    )
    rows = (await session.execute(threads)).tuples().all()
    if not rows:
        return []

    last_ids = [r.last_message_id for r in rows]
    messages = {
        m.id: m
        for m in (await session.execute(select(Message).where(Message.id.in_(last_ids))))
        .scalars()
        .all()
    }
    enr_contact: dict[str, tuple[Enrollment, Contact | None]] = {}
    joined = await session.execute(
        select(Enrollment, Contact)
        .outerjoin(Contact, Enrollment.contact_id == Contact.id)
        .where(Enrollment.id.in_([r.enrollment_id for r in rows]))
    )
    for enr, c in joined.tuples().all():
        enr_contact[enr.id] = (enr, c)

    items: list[InboxItemOut] = []
    for row in rows:
        last = messages.get(row.last_message_id)
        if last is None:
            continue
        pair = enr_contact.get(row.enrollment_id)
        enrollment = pair[0] if pair else None
        contact = pair[1] if pair else None
        has_unread = last.direction == MessageDirection.inbound and (
            enrollment is None
            or enrollment.last_read_at is None
            or (last.created_at is not None and last.created_at > enrollment.last_read_at)
        )
        items.append(
            InboxItemOut(
                enrollment_id=row.enrollment_id,
                contact_name=contact.full_name if contact else None,
                contact_title=contact.title if contact else None,
                contact_company=contact.company if contact else None,
                contact_avatar=contact.avatar_url if contact else None,
                state=enrollment.state if enrollment else None,
                outcome=enrollment.outcome if enrollment else None,
                reply_pending=bool(enrollment and enrollment.reply_pending),
                channel=row.first_channel,  # the channel the outreach started on
                message_count=int(row.message_count),
                unread=has_unread,
                last_at=last.created_at.isoformat() if last.created_at else None,
                last_message=dump_message(last),
            )
        )
    return items


class ConversationRefOut(BaseModel):
    """Which thread to open for a contact."""

    enrollment_id: str


@router.post("/contacts/{contact_id}/conversation", response_model=ConversationRefOut)
async def open_conversation(
    contact_id: str, ctx: ContextDep, session: SessionDep
) -> ConversationRefOut:
    """The conversation to open when a recruiter clicks Message on a person.

    Returns the existing thread with them if there is one, and otherwise opens a direct
    conversation — no campaign, no sequence. The client then navigates to it by id, which is what
    keeps "Message Lee" from landing on whoever happened to be at the top of the inbox.
    """
    ws = require_workspace(ctx)
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.workspace_id != ws:
        raise HTTPException(status_code=404, detail="contact not found")
    enrollment = await open_direct_conversation(session, workspace_id=ws, contact=contact)
    return ConversationRefOut(enrollment_id=enrollment.id)


@router.get("/inbox/{enrollment_id}", response_model=ConversationOut)
async def conversation(enrollment_id: str, ctx: ContextDep, session: SessionDep) -> ConversationOut:
    """Full conversation for the messenger: contact profile, campaign, state, channel, messages."""
    ws = require_workspace(ctx)
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id != ws:
        raise HTTPException(status_code=404, detail="conversation not found")
    contact = await session.get(Contact, enrollment.contact_id)
    campaign = (
        await session.get(Campaign, enrollment.campaign_id) if enrollment.campaign_id else None
    )
    messages = await list_thread(session, workspace_id=ws, enrollment_id=enrollment_id)
    # Primary channel = the channel of the most recent message.
    channel = messages[-1].channel if messages else Channel.email
    return ConversationOut(
        enrollment=ConvEnrollment(
            id=enrollment.id,
            state=enrollment.state,
            score=enrollment.score,
            current_step=enrollment.current_step,
            outcome=enrollment.outcome,
            reply_pending=enrollment.reply_pending,
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


@router.get("/inbox/{enrollment_id}/channels", response_model=ChannelsOut)
async def conversation_channels(
    enrollment_id: str, ctx: ContextDep, session: SessionDep
) -> ChannelsOut:
    """Which channels this conversation can be sent on, and which one the composer preselects.

    A direct conversation has no campaign behind it, so the seat is resolved from the caller
    instead. Requiring one here 404'd every thread opened by "Message" on a contact.
    """
    ws = require_workspace(ctx)
    enrollment = await _owned_enrollment(session, ws, enrollment_id)
    contact = await session.get(Contact, enrollment.contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    campaign = (
        await session.get(Campaign, enrollment.campaign_id) if enrollment.campaign_id else None
    )
    options = await channel_availability(
        session, campaign=campaign, contact=contact, user_id=ctx.user_id
    )
    default = await resolve_channel(
        session,
        campaign=campaign,
        enrollment_id=enrollment_id,
        contact=contact,
        user_id=ctx.user_id,
    )
    return ChannelsOut(
        default=default.value,
        options=[
            ChannelOptionOut(
                channel=o.channel.value, available=o.available, target=o.target, reason=o.reason
            )
            for o in options
        ],
    )


@router.post("/inbox/{enrollment_id}/reply", response_model=MessageOut)
async def send_reply(
    enrollment_id: str, body: SendRequest, ctx: ContextDep, session: SessionDep
) -> MessageOut:
    """Send a manual outbound message from the recruiter — over email or LinkedIn.

    `channel` picks the transport; omitted, the thread's existing channel is used. The message is
    delivered before it is recorded, so a failure surfaces as an error rather than a phantom
    "sent" bubble in the thread.

    Works on a direct conversation too (no campaign): the seat then comes from the sender.
    """
    ws = require_workspace(ctx)
    enrollment = await _owned_enrollment(session, ws, enrollment_id)
    contact = await session.get(Contact, enrollment.contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    campaign = (
        await session.get(Campaign, enrollment.campaign_id) if enrollment.campaign_id else None
    )
    channel = body.channel or await resolve_channel(
        session,
        campaign=campaign,
        enrollment_id=enrollment_id,
        contact=contact,
        user_id=ctx.user_id,
    )
    # Sending supersedes any AI-suggested draft in this thread — consume it so it doesn't linger.
    await session.execute(
        delete(Message).where(
            Message.enrollment_id == enrollment_id, Message.status == MessageStatus.draft
        )
    )
    message = await send_conversation_message(
        session,
        workspace_id=ws,
        enrollment=enrollment,
        campaign=campaign,
        contact=contact,
        channel=channel,
        subject=body.subject if channel == Channel.email else None,
        body=body.text,
        sender=(campaign.from_email if campaign else None) or get_settings().default_from_email,
        organization_id=ctx.org_id,
        now=datetime.now(UTC),
        origin="ai" if body.origin == "ai" else "human",
        user_id=ctx.user_id,
    )
    # The message is on the wire. Make the row recording it durable before doing anything else:
    # the audit write below, or the request's own commit, failing after a successful send used to
    # lose the thread's only trace of a message the candidate had already received.
    await session.commit()
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="reply.sent",
        summary=f"Sent a manual reply over {channel.value}",
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


class InboundWebhookOut(BaseModel):
    status: str
    intent: str | None


def _parsed_payload(raw: bytes) -> JsonObject:
    """The webhook body as a JSON object — 400 on anything that isn't parseable JSON."""
    try:
        parsed: object = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON") from None
    return parsed if isinstance(parsed, dict) else {}


def _reject_stale(payload: JsonObject) -> None:
    """Refuse a webhook whose own timestamp is outside the replay window.

    The timestamp is inside the signed body, so it can't be forged. Applied to both receivers: the
    Unipile one carries the production traffic and also accepts a bearer token from the query
    string, and its only replay defence was the `provider_message_id` dedupe — which returns None,
    and therefore records unconditionally, for any event with neither an id nor a timestamp.
    """
    stamp = payload.get("ts") or payload.get("timestamp")
    if isinstance(stamp, int | float) and not isinstance(stamp, bool):
        if abs(datetime.now(UTC).timestamp() - float(stamp)) > _WEBHOOK_MAX_SKEW_SECONDS:
            raise HTTPException(status_code=401, detail="stale webhook")


@router.post("/webhooks/inbound", response_model=InboundWebhookOut)
async def inbound_webhook(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> InboundWebhookOut:
    """System inbound from an email provider (HMAC-signed, no user session).

    Threads to an enrollment by `enrollment_id` or by the sender's email. Payload (JSON):
    `{"from": str, "text": str, "enrollment_id"?: str, "message_id"?: str}`, signed in the
    `X-Signature` header. Like the Unipile receiver this only records — the worker routes it.
    """
    secret = get_settings().inbound_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="inbound webhook not configured")
    raw = await request.body()
    if not verify_hmac(raw, request.headers.get("X-Signature"), secret=secret):
        raise HTTPException(status_code=401, detail="invalid signature")
    payload = _parsed_payload(raw)
    _reject_stale(payload)

    def _str(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    text = _str("text") or _str("body") or ""
    enrollment = await resolve_inbound_enrollment(
        session,
        from_email=_str("from") or _str("from_email") or "",
        enrollment_id=_str("enrollment_id"),
    )
    if enrollment is None or not text:
        return InboundWebhookOut(status="ignored", intent=None)
    message = await record_inbound(
        session,
        enrollment=enrollment,
        text=text,
        now=datetime.now(UTC),
        provider_message_id=receiving.idempotency_key(
            payload, thread_id=_str("thread_id"), text=text
        ),
    )
    if message is None:
        return InboundWebhookOut(status="duplicate", intent=None)
    return InboundWebhookOut(status="queued", intent=None)


# --- Unipile inbound (LinkedIn + email replies, account lifecycle) -----------


@router.post("/webhooks/unipile", response_model=InboundWebhookOut)
async def unipile_webhook(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> InboundWebhookOut:
    """Public Unipile receiver: inbound messages and account lifecycle events.

    Prefers an HMAC signature (`X-Unipile-Signature`) over the raw body; falls back to a shared
    token via the `X-Unipile-Token` header (or `?token=` for providers that can only template the
    URL).

    Everything past authentication is `services/outreach/receiving`. This handler used to carry a
    second, byte-identical copy of that module's payload readers, thread resolution and tenant
    scoping, plus its own inline reimplementation of `record_provider_event` — while the backfill
    sweep called the real one. Two copies of the receiver is how the webhook and the sweep start
    disagreeing about which replies belong to whom.

    A reply is only *recorded* here — classification and the Outreach agent run on the worker
    (`run_replies_due`). Unipile gets a fast ack, so it never times out and retries us into
    answering the same candidate twice; the `provider_message_id` guard catches redeliveries that
    happen anyway.
    """
    secret = get_settings().unipile_webhook_secret
    if not secret:
        raise HTTPException(status_code=503, detail="unipile webhook not configured")
    raw = await request.body()
    signature = request.headers.get("X-Unipile-Signature")
    if signature:
        if not verify_hmac(raw, signature, secret=secret):
            raise HTTPException(status_code=401, detail="invalid signature")
    else:
        token = request.headers.get("X-Unipile-Token") or request.query_params.get("token") or ""
        if not hmac.compare_digest(token, secret):
            raise HTTPException(status_code=401, detail="invalid token")
        # The token rides in a URL that proxies and CDNs log. Nothing is wrong with the request,
        # but a deployment leaking its receiver URL should be able to see that this path is live.
        logger.debug("unipile webhook: authenticated by shared token, not signature")
    payload = _parsed_payload(raw)
    _reject_stale(payload)
    now = datetime.now(UTC)

    account = await receiving.record_account_event(session, payload=payload, now=now)
    if account is not None:
        return InboundWebhookOut(status=account, intent=None)
    outcome = await receiving.record_provider_event(session, payload=payload, now=now)
    return InboundWebhookOut(status=outcome, intent=None)
