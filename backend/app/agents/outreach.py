"""The Outreach agent: hold a live candidate conversation (with human-in-the-loop).

Triggered by an inbound reply. The agent reads the thread and decides: reply (answer/qualify),
hand off (interested / negotiation / out-of-scope / unsure), or opt out. HITL has three layers —
the `reply` tool's autonomy gate (full → send, else → queue a draft), the handoff (always to the
human), and opt-out (always auto, for compliance). `handle_reply` falls back to the deterministic
classify+route path when no LLM is available.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import compose_system
from app.core import policy
from app.core.runtime import AgentLLM, AgentResult, Tool, default_llm, run_agent
from app.core.types import JsonList, JsonObject
from app.models import (
    AgentRole,
    AutonomyLevel,
    Campaign,
    Channel,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    SuppressionReason,
)
from app.services.outreach.messaging import (
    PermanentSendError,
    TransientSendError,
    deliver_outbound,
    ingest_reply,
    resolve_channel_seat,
)
from app.services.sourcing import suppression

_DEFAULT_SENDER = "recruiter@sourcewell.dev"


@dataclass
class ConversationContext:
    session: AsyncSession
    enrollment: Enrollment
    campaign: Campaign
    contact: Contact
    organization_id: str


def _str(data: JsonObject, key: str) -> str | None:
    v = data.get(key)
    return v if isinstance(v, str) else None


def conversation_tools(ctx: ConversationContext) -> list[Tool]:
    """The Outreach agent's per-thread toolset (bound to one enrollment)."""

    async def get_thread(data: JsonObject) -> JsonObject:
        rows = await ctx.session.execute(
            select(Message)
            .where(Message.enrollment_id == ctx.enrollment.id)
            .order_by(Message.created_at)
        )
        msgs: JsonList = [
            {"direction": m.direction.value, "body": m.body} for m in rows.scalars().all()
        ]
        return {"messages": msgs}

    async def reply(data: JsonObject) -> JsonObject:
        text = _str(data, "text") or ""
        if not text:
            return {"error": "empty reply"}
        channel = Channel.email if ctx.contact.email else Channel.linkedin
        auto = ctx.campaign.autonomy_level == AutonomyLevel.full
        message = Message(
            workspace_id=ctx.enrollment.workspace_id,
            enrollment_id=ctx.enrollment.id,
            direction=MessageDirection.outbound,
            channel=channel,
            status=MessageStatus.sent if auto else MessageStatus.draft,
            subject="Re:",
            body=text,
            origin="ai",
        )
        ctx.session.add(message)
        if auto:
            seat = await resolve_channel_seat(ctx.session, campaign=ctx.campaign, channel=channel)
            try:
                await deliver_outbound(
                    ctx.session,
                    message=message,
                    contact=ctx.contact,
                    seat=seat,
                    sender=ctx.campaign.from_email or _DEFAULT_SENDER,
                    reply=True,
                )
                message.sent_at = datetime.now(UTC)
            except (PermanentSendError, TransientSendError):
                message.status = MessageStatus.failed
            ctx.enrollment.state = EnrollmentState.awaiting_reply
            ctx.enrollment.reply_pending = False
        else:
            ctx.enrollment.state = EnrollmentState.awaiting_approval
        await ctx.session.flush()
        return {"replied": True, "sent": auto}

    async def hand_off(data: JsonObject) -> JsonObject:
        ctx.enrollment.state = EnrollmentState.handed_off
        ctx.enrollment.outcome = "interested"
        ctx.enrollment.next_run_at = None
        ctx.enrollment.reply_pending = False
        await ctx.session.flush()
        return {"handed_off": True}

    async def opt_out(data: JsonObject) -> JsonObject:
        ctx.enrollment.state = EnrollmentState.opted_out
        ctx.enrollment.outcome = "opted_out"
        ctx.enrollment.next_run_at = None
        ctx.enrollment.reply_pending = False
        if ctx.contact.email:
            await suppression.suppress(
                ctx.session,
                organization_id=ctx.organization_id,
                email=ctx.contact.email,
                reason=SuppressionReason.opted_out,
                contact_id=ctx.contact.id,
            )
        await ctx.session.flush()
        return {"opted_out": True}

    obj = "object"
    return [
        Tool(
            "get_thread",
            "Read the full message history with this candidate.",
            {"type": obj},
            get_thread,
        ),
        Tool(
            "reply",
            "Reply to the candidate (auto-sends at full autonomy, else queues a draft).",
            {"type": obj, "properties": {"text": {"type": "string"}}, "required": ["text"]},
            reply,
        ),
        Tool(
            "hand_off",
            "Hand the warm thread to the human (interested / negotiation / out-of-scope).",
            {"type": obj, "properties": {"summary": {"type": "string"}}},
            hand_off,
        ),
        Tool(
            "opt_out",
            "Honor an opt-out: stop the sequence and suppress the contact.",
            {"type": obj},
            opt_out,
        ),
    ]


async def run_conversation(
    session: AsyncSession,
    *,
    llm: AgentLLM,
    enrollment: Enrollment,
    inbound_text: str,
    organization_id: str,
    now: datetime,
    channel: Channel = Channel.email,
    provider_message_id: str | None = None,
) -> AgentResult:
    """Record an inbound reply and run one bounded Outreach conversation."""
    session.add(
        Message(
            workspace_id=enrollment.workspace_id,
            enrollment_id=enrollment.id,
            direction=MessageDirection.inbound,
            channel=channel,
            status=MessageStatus.received,
            body=inbound_text,
            created_at=now,
            provider_message_id=provider_message_id,
        )
    )
    await session.flush()

    campaign = await session.get(Campaign, enrollment.campaign_id)
    contact = await session.get(Contact, enrollment.contact_id)
    if campaign is None or contact is None:
        raise ValueError("enrollment is missing its campaign or contact")
    vertical = (await policy.for_campaign(session, campaign=campaign)).get_str("vertical")
    ctx = ConversationContext(
        session=session,
        enrollment=enrollment,
        campaign=campaign,
        contact=contact,
        organization_id=organization_id,
    )
    user = (
        f"The candidate {contact.full_name} replied:\n{inbound_text!r}\n\n"
        "Read the thread, then decide: reply (answer/qualify), hand_off (interested, negotiation, "
        "out-of-scope, or unsure), or opt_out (they asked to stop)."
    )
    return await run_agent(
        session,
        llm=llm,
        role=AgentRole.outreach,
        trigger="reply",
        workspace_id=enrollment.workspace_id,
        campaign_id=campaign.id,
        system=compose_system(AgentRole.outreach, vertical),
        user_prompt=user,
        tools=conversation_tools(ctx),
    )


async def handle_reply(
    session: AsyncSession,
    *,
    enrollment: Enrollment,
    text: str,
    now: datetime,
    organization_id: str,
    llm: AgentLLM | None = None,
    channel: Channel = Channel.email,
    provider_message_id: str | None = None,
) -> str:
    """Route an inbound reply: the Outreach agent when an LLM is available, else deterministic."""
    client = llm if llm is not None else default_llm()
    if client is None:
        await ingest_reply(
            session,
            workspace_id=enrollment.workspace_id,
            enrollment_id=enrollment.id,
            text=text,
            now=now,
            channel=channel,
            provider_message_id=provider_message_id,
        )
        return "deterministic"
    await run_conversation(
        session,
        llm=client,
        enrollment=enrollment,
        inbound_text=text,
        organization_id=organization_id,
        now=now,
        channel=channel,
        provider_message_id=provider_message_id,
    )
    return "agent"
