"""Messaging HTTP layer: routes, schemas, serializers (approvals / inbox / webhooks)."""

import hmac
import json
from datetime import UTC, datetime
from hashlib import sha256
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.core.config import get_settings
from app.core.crypto import verify_hmac
from app.core.db import get_session
from app.core.logging import logger
from app.core.types import JsonObject
from app.models import (
    ORG_WIDE_ROLES,
    Campaign,
    Channel,
    Connection,
    ConnectionStatus,
    Contact,
    Enrollment,
    EnrollmentState,
    Membership,
    Message,
    MessageDirection,
    MessageStatus,
    SpaceGrant,
    Workspace,
)
from app.services.insights import audit
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
from app.services.outreach.receiving import strip_quoted_reply

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


@router.get("/inbox", response_model=list[InboxItemOut])
async def inbox(ctx: ContextDep, session: SessionDep) -> list[InboxItemOut]:
    ws = require_workspace(ctx)
    rows = await session.execute(
        select(Message).where(Message.workspace_id == ws).order_by(Message.created_at)
    )
    by_enrollment: dict[str, list[Message]] = {}
    for m in rows.scalars().all():
        by_enrollment.setdefault(m.enrollment_id, []).append(m)

    # Batch-load each thread's enrollment + contact in one query (was an N+1 of 2 gets per thread).
    enr_contact: dict[str, tuple[Enrollment, Contact | None]] = {}
    if by_enrollment:
        joined = await session.execute(
            select(Enrollment, Contact)
            .outerjoin(Contact, Enrollment.contact_id == Contact.id)
            .where(Enrollment.id.in_(by_enrollment.keys()))
        )
        for enr, c in joined.tuples().all():
            enr_contact[enr.id] = (enr, c)

    items: list[InboxItemOut] = []
    for enrollment_id, messages in by_enrollment.items():
        pair = enr_contact.get(enrollment_id)
        enrollment = pair[0] if pair else None
        contact = pair[1] if pair else None
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
                state=enrollment.state if enrollment else None,
                outcome=enrollment.outcome if enrollment else None,
                reply_pending=bool(enrollment and enrollment.reply_pending),
                channel=messages[0].channel,  # the channel the outreach started on
                message_count=len(messages),
                unread=has_unread,
                last_at=last.created_at.isoformat() if last.created_at else None,
                last_message=dump_message(last),
            )
        )
    items.sort(key=lambda it: it.last_at or "", reverse=True)
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
    """Which channels this conversation can be sent on, and which one the composer preselects."""
    ws = require_workspace(ctx)
    enrollment = await _owned_enrollment(session, ws, enrollment_id)
    contact = await session.get(Contact, enrollment.contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    campaign = (
        await session.get(Campaign, enrollment.campaign_id) if enrollment.campaign_id else None
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    options = await channel_availability(session, campaign=campaign, contact=contact)
    default = await resolve_channel(
        session, campaign=campaign, enrollment_id=enrollment_id, contact=contact
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
    """
    ws = require_workspace(ctx)
    enrollment = await _owned_enrollment(session, ws, enrollment_id)
    contact = await session.get(Contact, enrollment.contact_id)
    if contact is None:
        raise HTTPException(status_code=404, detail="contact not found")
    campaign = (
        await session.get(Campaign, enrollment.campaign_id) if enrollment.campaign_id else None
    )
    if campaign is None:
        raise HTTPException(status_code=404, detail="campaign not found")
    channel = body.channel or await resolve_channel(
        session, campaign=campaign, enrollment_id=enrollment_id, contact=contact
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
        sender=campaign.from_email or get_settings().default_from_email,
        organization_id=ctx.org_id,
        now=datetime.now(UTC),
        origin="ai" if body.origin == "ai" else "human",
    )
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
    try:
        parsed: object = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON") from None
    payload: JsonObject = parsed if isinstance(parsed, dict) else {}

    def _str(key: str) -> str | None:
        value = payload.get(key)
        return value if isinstance(value, str) else None

    # Replay guard: a signed payload with a stale timestamp is rejected (the `ts` is inside the
    # HMAC, so it can't be forged); exact resends are also dropped by provider_message_id dedupe.
    ts = payload.get("ts") or payload.get("timestamp")
    if isinstance(ts, int | float) and not isinstance(ts, bool):
        if abs(datetime.now(UTC).timestamp() - float(ts)) > _WEBHOOK_MAX_SKEW_SECONDS:
            raise HTTPException(status_code=401, detail="stale webhook")

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
        provider_message_id=_idempotency_key(payload, thread_id=_str("thread_id"), text=text),
    )
    if message is None:
        return InboundWebhookOut(status="duplicate", intent=None)
    return InboundWebhookOut(status="queued", intent=None)


# --- Unipile inbound (LinkedIn + email replies, account lifecycle) -----------


def _is_own_message(payload: JsonObject) -> bool:
    """Did this seat send the message the provider is telling us about?

    Unipile pushes *every* message in a chat, including the ones we sent — so without this the
    receiver records our own outreach as the candidate's reply: a fabricated inbound bubble, a
    sequence stuck waiting on a reply that already "arrived", and at full autonomy an agent
    answering its own message. `is_sender` is the provider's own flag for it.
    """
    for source in (payload.get("message"), payload):
        if not isinstance(source, dict):
            continue
        flag = source.get("is_sender")
        if isinstance(flag, bool):
            return flag
        if isinstance(flag, int):
            return flag == 1
        if isinstance(flag, str) and flag.strip().lower() in {"true", "1"}:
            return True
    return False


def _idempotency_key(payload: JsonObject, *, thread_id: str | None, text: str) -> str | None:
    """A stable id for this inbound event, so a redelivery is recognised and dropped.

    Prefer the provider's own message id. When there isn't one, fall back to a digest of the
    event's identifying parts *including the provider timestamp* — a redelivery hashes the same,
    while a candidate genuinely sending "ok" twice does not (different timestamps). With neither
    an id nor a timestamp the digest can't separate those two cases, so we return None and record
    unconditionally: a rare duplicate bubble beats silently dropping a real reply.
    """
    message_id = _payload_str(payload.get("message"), "id", "message_id") or _payload_str(
        payload, "message_id", "id"
    )
    if message_id:
        return message_id
    stamp = _payload_str(payload.get("message"), "timestamp", "date", "created_at") or _payload_str(
        payload, "timestamp", "date", "created_at"
    )
    if not stamp:
        return None
    digest = sha256("\x00".join([thread_id or "", stamp, text]).encode()).hexdigest()
    return f"sha256:{digest}"


def _payload_str(obj: object, *keys: str) -> str | None:
    """First non-empty string value among `keys` of a dict-ish payload, else None."""
    if not isinstance(obj, dict):
        return None
    for key in keys:
        value = obj.get(key)
        if isinstance(value, str) and value:
            return value
    return None


async def _seat_workspace_ids(session: AsyncSession, account_id: str | None) -> list[str] | None:
    """Workspaces the seat that received this event can send from, or None when it's unknown.

    Mirrors the access rules in `api/context.py`: the seat's owner reaches every workspace in an
    organization where they are org_admin or compliance, plus the ones an explicit grant names.
    `None` means we couldn't identify the seat at all — the caller then has to fall back to a
    weaker rule rather than a wrong one.
    """
    if not account_id:
        return None
    org_wide = (
        select(Membership.organization_id)
        .join(Connection, Connection.user_id == Membership.user_id)
        .where(Connection.external_id == account_id, Membership.role.in_(ORG_WIDE_ROLES))
    )
    granted = (
        select(SpaceGrant.workspace_id)
        .join(Connection, Connection.user_id == SpaceGrant.user_id)
        .where(Connection.external_id == account_id)
    )
    rows = (
        await session.execute(
            select(Workspace.id).where(
                or_(Workspace.organization_id.in_(org_wide), Workspace.id.in_(granted))
            )
        )
    ).scalars()
    return list(dict.fromkeys(rows)) or None


async def _resolve_enrollment(
    session: AsyncSession,
    *,
    external_id: str | None,
    sender_email: str | None,
    account_id: str | None = None,
) -> tuple[Enrollment, Channel] | None:
    """Map a Unipile event to (enrollment, channel): the chat we sent on, else the sender address.

    The channel comes from the outbound message the thread id matched, so a LinkedIn reply is
    recorded as LinkedIn rather than defaulting to email.

    Both lookups are scoped to the workspaces of the seat that received the event. Without that
    scope the address fallback spans every tenant, and two customers working the same candidate
    cross over — one of them silently receiving the other's reply.
    """
    scope = await _seat_workspace_ids(session, account_id)
    if external_id:
        stmt = select(Message).where(Message.external_id == external_id)
        if scope is not None:
            stmt = stmt.where(Message.workspace_id.in_(scope))
        msg = (
            (await session.execute(stmt.order_by(Message.created_at.desc()).limit(1)))
            .scalars()
            .first()
        )
        if msg is not None:
            enrollment = await session.get(Enrollment, msg.enrollment_id)
            if enrollment is not None:
                return enrollment, msg.channel
    if sender_email:
        by_sender = (
            select(Enrollment)
            .join(Contact, Enrollment.contact_id == Contact.id)
            .where(func.lower(Contact.email) == sender_email.strip().lower())
        )
        if scope is not None:
            by_sender = by_sender.where(Enrollment.workspace_id.in_(scope))
        candidates = list(
            (await session.execute(by_sender.order_by(Enrollment.created_at.desc())))
            .scalars()
            .all()
        )
        enrollment = _unambiguous(candidates, sender_email)
        if enrollment is not None:
            # No thread id to read a channel off: this arrived at the email receiver.
            return enrollment, Channel.email
    return None


def _unambiguous(candidates: list[Enrollment], sender_email: str) -> Enrollment | None:
    """The newest candidate — unless they straddle workspaces, in which case there is no answer.

    Guessing across a tenant boundary is the one outcome worse than dropping the reply: it shows
    one customer's candidate reply inside another's inbox. Ambiguity here means the event carried
    no seat we could scope by, so we log it loudly instead of picking.
    """
    if not candidates:
        return None
    workspaces = {e.workspace_id for e in candidates}
    if len(workspaces) > 1:
        logger.warning(
            "inbound: %d enrollments across %d workspaces match sender %s — dropping, "
            "no seat to disambiguate",
            len(candidates),
            len(workspaces),
            sender_email,
        )
        return None
    return candidates[0]


def _drop(why: str, payload: JsonObject) -> InboundWebhookOut:
    """Log an inbound event we couldn't place, and report it as ignored.

    A dropped reply is otherwise completely silent — the provider gets its 200 and the recruiter
    never learns the candidate wrote back. The payload's *keys* go in the log, not its contents:
    enough to see that a field we expected is named something else, without copying message
    bodies or addresses into the logs.
    """
    logger.warning("inbound: dropping an event — %s; payload keys=%s", why, sorted(payload))
    return InboundWebhookOut(status="ignored", intent=None)


@router.post("/webhooks/unipile", response_model=InboundWebhookOut)
async def unipile_webhook(
    request: Request, session: Annotated[AsyncSession, Depends(get_session)]
) -> InboundWebhookOut:
    """Public Unipile receiver: inbound messages and account lifecycle events.

    Prefers an HMAC signature (`X-Unipile-Signature`) over the raw body; falls back to a shared
    token via the `X-Unipile-Token` header (or `?token=` for providers that can only template the
    URL).

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
    try:
        parsed: object = json.loads(raw or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON") from None
    payload: JsonObject = parsed if isinstance(parsed, dict) else {}
    now = datetime.now(UTC)

    event = (_payload_str(payload, "event", "type", "status") or "").lower()
    account_id = _payload_str(payload, "account_id", "account")

    # Account lifecycle: a credentials / disconnect event flips the seat to needs-reauth.
    if account_id and ("credential" in event or "disconnect" in event or "error" in event):
        seat = (
            (
                await session.execute(
                    select(Connection).where(Connection.external_id == account_id).limit(1)
                )
            )
            .scalars()
            .first()
        )
        if seat is not None and seat.status is not ConnectionStatus.needs_reauth:
            seat.status = ConnectionStatus.needs_reauth
            await session.flush()
            # The seat owner sees this in their notification feed; the audit trail is what tells
            # an admin *when* a channel went quiet, which is otherwise invisible after the fact.
            await audit.record(
                session,
                org_id=seat.organization_id,
                workspace_id=None,
                actor_user_id=seat.user_id,
                action="connection.needs_reauth",
                summary=f"{seat.provider.value} seat disconnected at the provider",
                target_type="connection",
                target_id=seat.id,
            )
        return InboundWebhookOut(status="account_updated", intent=None)

    # Inbound message: text + the chat/thread id (LinkedIn) or sender (email).
    # `body_plain` first — an email carries both, and `body` is the HTML part: recording that puts
    # markup on the thread and feeds tags to the reply classifier.
    text = (
        _payload_str(payload.get("message"), "text", "body")
        or _payload_str(payload, "body_plain")
        or _payload_str(payload, "text", "body", "message")
    )
    chat_id = _payload_str(payload, "chat_id", "chat", "thread_id")
    # Unipile names the sender differently per channel: an email carries `from_attendee`
    # (`{display_name, identifier}`), a chat message a `sender`. Reading only the chat shape meant
    # every email reply resolved to nobody and was dropped — with a 200 back to the provider, so
    # it looked delivered from both ends.
    sender_email = (
        _payload_str(payload.get("from_attendee"), "identifier", "email")
        or _payload_str(payload.get("sender"), "email", "identifier")
        or _payload_str(payload, "from", "from_email")
    )
    if not text:
        return _drop("no text in the event", payload)
    if _is_own_message(payload):
        # Our own outbound, echoed back by the provider. Recording it would invent a reply.
        return InboundWebhookOut(status="own_message", intent=None)
    resolved = await _resolve_enrollment(
        session, external_id=chat_id, sender_email=sender_email, account_id=account_id
    )
    if resolved is None:
        return _drop(f"no thread matched (chat_id={chat_id!r}, sender={sender_email!r})", payload)
    # The channel comes from `_resolve_enrollment`, which reads it off the outbound message the
    # thread id matched (and falls back to email for an address match) — always a real Channel,
    # so the old `matched_channel or payload_channel` fallback could never fire.
    enrollment, matched_channel = resolved
    message = await record_inbound(
        session,
        enrollment=enrollment,
        # Email only: a LinkedIn DM has no quoted history, and a chat message that happens to
        # start a line with ">" would be truncated for nothing.
        text=strip_quoted_reply(text) if matched_channel is Channel.email else text,
        now=now,
        channel=matched_channel,
        provider_message_id=_idempotency_key(payload, thread_id=chat_id, text=text),
        external_id=chat_id,
    )
    if message is None:
        return InboundWebhookOut(status="duplicate", intent=None)
    return InboundWebhookOut(status="queued", intent=None)
