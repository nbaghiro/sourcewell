"""The Outreach agent: hold a live candidate conversation (with human-in-the-loop).

Triggered by an inbound reply. The agent reads the thread and decides: reply (answer/qualify),
hand off (interested / negotiation / out-of-scope / unsure), or opt out. HITL has three layers —
the `reply` tool's autonomy gate (full → send, else → queue a draft), the handoff (always to the
human), and opt-out (always auto, for compliance). `handle_reply` falls back to the deterministic
classify+route path when no LLM is available.
"""

from dataclasses import dataclass
from datetime import datetime

from fastapi import HTTPException
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
    resolve_channel,
    route_inbound,
    send_conversation_message,
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
    now: datetime


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
        # Stay on the thread's own channel (falling back if it can't carry a message any more)
        # rather than assuming email whenever an address exists.
        channel = await resolve_channel(
            ctx.session,
            campaign=ctx.campaign,
            enrollment_id=ctx.enrollment.id,
            contact=ctx.contact,
        )

        async def queue_for_approval() -> None:
            """Park the reply as a draft in the approval queue instead of sending it."""
            ctx.session.add(
                Message(
                    workspace_id=ctx.enrollment.workspace_id,
                    enrollment_id=ctx.enrollment.id,
                    direction=MessageDirection.outbound,
                    channel=channel,
                    status=MessageStatus.draft,
                    subject="Re:" if channel == Channel.email else None,
                    body=text,
                    origin="ai",
                )
            )
            ctx.enrollment.state = EnrollmentState.awaiting_approval
            await ctx.session.flush()

        if ctx.campaign.autonomy_level != AutonomyLevel.full:
            await queue_for_approval()
            return {"replied": True, "sent": False}
        try:
            await send_conversation_message(
                ctx.session,
                workspace_id=ctx.enrollment.workspace_id,
                enrollment=ctx.enrollment,
                campaign=ctx.campaign,
                contact=ctx.contact,
                channel=channel,
                subject="Re:" if channel == Channel.email else None,
                body=text,
                sender=ctx.campaign.from_email or _DEFAULT_SENDER,
                organization_id=ctx.organization_id,
                now=ctx.now,
            )
        except HTTPException as exc:
            # Undeliverable at full autonomy: queue it for a human instead of dropping the reply.
            await queue_for_approval()
            return {"replied": True, "sent": False, "error": str(exc.detail)}
        ctx.enrollment.state = EnrollmentState.awaiting_reply
        await ctx.session.flush()
        return {"replied": True, "sent": True}

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
    message: Message,
    organization_id: str,
    now: datetime,
    channel: Channel = Channel.email,
    provider_message_id: str | None = None,
) -> AgentResult:
    """Run one bounded Outreach conversation over an already-recorded inbound reply.

    The reply is written to the thread by the receiver (`messaging.record_inbound`) before this
    runs — recording is what the provider webhook acknowledges, and it carries the idempotency
    key. Re-recording here would duplicate the bubble on every worker retry.
    """
    inbound_text = message.body
    campaign = (
        await session.get(Campaign, enrollment.campaign_id) if enrollment.campaign_id else None
    )
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
        now=now,
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
    message: Message,
    now: datetime,
    organization_id: str,
    llm: AgentLLM | None = None,
    channel: Channel = Channel.email,
    provider_message_id: str | None = None,
) -> str:
    """Route a recorded inbound reply: the Outreach agent when an LLM is available, else the
    deterministic classify-and-transition path.

    Marks the message routed either way, so the worker's sweep won't pick it up again.
    """
    client = llm if llm is not None else default_llm()
    if client is None:
        await route_inbound(session, enrollment=enrollment, message=message, now=now)
        return "deterministic"
    await run_conversation(
        session,
        llm=client,
        enrollment=enrollment,
        message=message,
        organization_id=organization_id,
        now=now,
        channel=channel,
        provider_message_id=provider_message_id,
    )
    message.processed_at = now
    await session.flush()
    return "agent"
