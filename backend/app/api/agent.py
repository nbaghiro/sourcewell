"""The agent experience read-model + chat HTTP layer — a unified, humanized view of what the agent
is doing.

Synthesized from what we already persist (enrollments, messages, governor settings) — no new
tables. Every agent-experience UI surface (activity feed, mission control, daily briefing, chat)
renders off these endpoints, so the comparison is on UX, not data. Thin: each endpoint calls a
service and maps the returned dataclass to the Pydantic response model.
"""

import json
from collections.abc import AsyncIterator

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat import run_chat, run_chat_stream
from app.agents.main import design_campaign, deterministic_design
from app.agents.prompts import DEFAULT_VERTICAL
from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.core.db import SessionLocal
from app.core.runtime import default_llm
from app.core.types import JsonList, JsonObject
from app.models import Campaign, Workspace
from app.services.cockpit.activity import ActivityEventData, RefData, build_activity_stream
from app.services.cockpit.runs import campaign_funnel, recent_runs
from app.services.cockpit.state import StateData, aggregate_state
from app.services.sourcing.briefs import parse_brief

router = APIRouter(prefix="/agent", tags=["agent"])


# ---- shapes ----------------------------------------------------------------


class Ref(BaseModel):
    id: str
    name: str
    sub: str | None = None
    avatar: str | None = None


class ActivityEvent(BaseModel):
    id: str
    ts: str
    kind: str  # sourced | drafted | scheduled | sent | reply | handed_off | opted_out | completed
    title: str
    detail: str | None = None
    rationale: str | None = None
    contact: Ref | None = None
    campaign: Ref | None = None


class GovernorChannel(BaseModel):
    cap: int
    sent: int
    blocked: bool


class AgentCampaign(BaseModel):
    id: str
    name: str
    status: str
    active: int


class AgentState(BaseModel):
    status: str  # active | idle
    counts: dict[str, int]
    today: dict[str, int]
    needs_you: dict[str, int]
    governor: dict[str, GovernorChannel]
    campaigns: list[AgentCampaign]


class ChatIn(BaseModel):
    message: str
    campaign_id: str | None = None


class ChatOut(BaseModel):
    reply: str
    kind: str  # status | explain | find | help | agent
    data: JsonObject | None = None
    entities: JsonList = []  # typed UI blocks (catalog §12) — populated by the Main-agent chat


def _ref(r: RefData | None) -> Ref | None:
    return None if r is None else Ref(id=r.id, name=r.name, sub=r.sub, avatar=r.avatar)


def _activity_event(e: ActivityEventData) -> ActivityEvent:
    return ActivityEvent(
        id=e.id,
        ts=e.ts,
        kind=e.kind,
        title=e.title,
        detail=e.detail,
        rationale=e.rationale,
        contact=_ref(e.contact),
        campaign=_ref(e.campaign),
    )


def _agent_state(st: StateData) -> AgentState:
    return AgentState(
        status=st.status,
        counts=st.counts,
        today=st.today,
        needs_you=st.needs_you,
        governor={
            ch: GovernorChannel(cap=g.cap, sent=g.sent, blocked=g.blocked)
            for ch, g in st.governor.items()
        },
        campaigns=[
            AgentCampaign(id=c.id, name=c.name, status=c.status, active=c.active)
            for c in st.campaigns
        ],
    )


# ---- activity --------------------------------------------------------------


@router.get("/activity", response_model=list[ActivityEvent])
async def activity(ctx: ContextDep, session: SessionDep, limit: int = 40) -> list[ActivityEvent]:
    """A merged, humanized stream of the agent's recent actions, newest first."""
    ws = require_workspace(ctx)
    events = await build_activity_stream(session, workspace_id=ws, limit=limit)
    return [_activity_event(e) for e in events]


# ---- state -----------------------------------------------------------------


@router.get("/state", response_model=AgentState)
async def state(ctx: ContextDep, session: SessionDep) -> AgentState:
    """Live snapshot: queue counts, today's throughput, what needs the human, governor headroom."""
    ws = require_workspace(ctx)
    return _agent_state(await aggregate_state(session, workspace_id=ws))


# ---- chat ------------------------------------------------------------------


@router.post("/chat", response_model=ChatOut)
async def chat(body: ChatIn, ctx: ContextDep, session: SessionDep) -> ChatOut:
    """Main-agent chat: text + typed entities (catalog §12). Requires an LLM."""
    ws = require_workspace(ctx)
    client = default_llm()
    if client is None:
        return ChatOut(
            reply="Chat needs an AI model connected. Use Find People or Approvals meanwhile.",
            kind="help",
            data=None,
            entities=[],
        )
    res = await run_chat(
        session,
        llm=client,
        workspace_id=ws,
        organization_id=ctx.org_id,
        message=body.message,
        campaign_id=body.campaign_id,
    )
    return ChatOut(reply=res.reply, kind="agent", data=None, entities=res.entities)


def _sse(obj: JsonObject) -> str:
    return f"data: {json.dumps(obj)}\n\n"


@router.post("/chat/stream")
async def chat_stream(body: ChatIn, ctx: ContextDep) -> StreamingResponse:
    """Streaming Main-agent chat (SSE): `token` events as the narration streams, then a `done`
    event carrying the typed entities. Requires an LLM.
    """
    ws = require_workspace(ctx)
    org_id = ctx.org_id
    client = default_llm()

    async def gen() -> AsyncIterator[str]:
        if client is None:
            yield _sse({"type": "token", "text": "Chat needs an AI model connected."})
            yield _sse({"type": "done", "entities": []})
            return
        # the request-scoped session is closed by the time this body streams — use a fresh one.
        async with SessionLocal() as session:
            async for ev in run_chat_stream(
                session,
                llm=client,
                workspace_id=ws,
                organization_id=org_id,
                message=body.message,
                campaign_id=body.campaign_id,
            ):
                yield _sse(ev)
            await session.commit()

    return StreamingResponse(gen(), media_type="text/event-stream")


# ---- per-campaign agent runs + funnel (the cockpit Activity tab + header) ---


class AgentStepOut(BaseModel):
    seq: int
    kind: str
    tool_name: str | None
    content: JsonObject


class AgentRunOut(BaseModel):
    id: str
    role: str
    trigger: str
    status: str
    summary: str
    tokens: int
    created_at: str
    steps: list[AgentStepOut]


class CampaignFunnelOut(BaseModel):
    sourced: int
    contacted: int
    replied: int
    handed_off: int


async def _campaign_in_workspace(
    session: AsyncSession, campaign_id: str, workspace_id: str
) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="campaign not found")
    return campaign


@router.get("/runs", response_model=list[AgentRunOut])
async def runs(
    ctx: ContextDep, session: SessionDep, campaign_id: str, limit: int = 20
) -> list[AgentRunOut]:
    """The agent-episode trace feed for a campaign — the narrated activity tab."""
    ws = require_workspace(ctx)
    await _campaign_in_workspace(session, campaign_id, ws)
    data = await recent_runs(session, campaign_id=campaign_id, limit=limit)
    return [
        AgentRunOut(
            id=r.id,
            role=r.role,
            trigger=r.trigger,
            status=r.status,
            summary=r.summary,
            tokens=r.tokens,
            created_at=r.created_at,
            steps=[
                AgentStepOut(seq=s.seq, kind=s.kind, tool_name=s.tool_name, content=s.content)
                for s in r.steps
            ],
        )
        for r in data
    ]


@router.get("/funnel", response_model=CampaignFunnelOut)
async def funnel(ctx: ContextDep, session: SessionDep, campaign_id: str) -> CampaignFunnelOut:
    """The per-campaign funnel rollup — the cockpit header."""
    ws = require_workspace(ctx)
    await _campaign_in_workspace(session, campaign_id, ws)
    f = await campaign_funnel(session, campaign_id=campaign_id)
    return CampaignFunnelOut(
        sourced=f.sourced, contacted=f.contacted, replied=f.replied, handed_off=f.handed_off
    )


# ---- brief intake + the Main agent's design (the create flow) --------------


class IntakeIn(BaseModel):
    text: str


class IntakeOut(BaseModel):
    objective: str
    criteria: JsonObject
    facts: JsonObject


@router.post("/intake", response_model=IntakeOut)
async def intake(body: IntakeIn, ctx: ContextDep, session: SessionDep) -> IntakeOut:
    """Parse a JD / brief into an objective + targeting (the create flow's step 0)."""
    ws = require_workspace(ctx)
    workspace = await session.get(Workspace, ws)
    vertical = workspace.vertical if workspace else DEFAULT_VERTICAL
    brief = await parse_brief(body.text, vertical=vertical)
    return IntakeOut(
        objective=brief.objective, criteria=brief.targeting.model_dump(), facts=brief.facts
    )


class DesignIn(BaseModel):
    campaign_id: str


class DesignOut(BaseModel):
    status: str
    criteria: JsonObject
    sequence: JsonList


@router.post("/design", response_model=DesignOut)
async def design(body: DesignIn, ctx: ContextDep, session: SessionDep) -> DesignOut:
    """Run the Main agent's cold-start design (LLM-free fallback when no key)."""
    ws = require_workspace(ctx)
    campaign = await _campaign_in_workspace(session, body.campaign_id, ws)
    client = default_llm()
    if client is not None:
        result = await design_campaign(
            session, llm=client, campaign=campaign, organization_id=ctx.org_id
        )
        status = result.status
    else:
        await deterministic_design(session, campaign=campaign, organization_id=ctx.org_id)
        status = "done"
    return DesignOut(status=status, criteria=campaign.criteria, sequence=campaign.sequence)


class ApplyAudienceIn(BaseModel):
    campaign_id: str
    criteria: JsonObject


@router.post("/apply-audience", response_model=DesignOut)
async def apply_audience(body: ApplyAudienceIn, ctx: ContextDep, session: SessionDep) -> DesignOut:
    """Apply a previewed audience (a human action) — set the criteria and pin the section."""
    ws = require_workspace(ctx)
    campaign = await _campaign_in_workspace(session, body.campaign_id, ws)
    campaign.criteria = body.criteria
    campaign.field_owners = {**campaign.field_owners, "audience": "human"}
    await session.flush()
    return DesignOut(status="applied", criteria=campaign.criteria, sequence=campaign.sequence)
