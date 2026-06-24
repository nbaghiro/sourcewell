"""The agent experience read-model + chat HTTP layer — a unified, humanized view of what the agent
is doing.

Synthesized from what we already persist (enrollments, messages, governor settings) — no new
tables. Every agent-experience UI variant (activity feed, mission control, daily briefing, copilot
chat) renders off these endpoints, so the comparison is on UX, not data. Thin: each endpoint calls
a service and maps the returned dataclass to the Pydantic response model.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.core.types import JsonObject
from app.deps import ContextDep, SessionDep, require_workspace
from app.services.agent.activity import ActivityEventData, RefData, build_activity_stream
from app.services.agent.chat import handle_chat
from app.services.agent.state import StateData, aggregate_state

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


class ChatOut(BaseModel):
    reply: str
    kind: str  # status | explain | find | help
    data: JsonObject | None = None


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
    """A bounded copilot: answers about state, explains a person, previews a search. No destructive
    actions yet — those are a fast-follow once the chat direction is validated."""
    result = await handle_chat(session, ctx, message=body.message)
    return ChatOut(reply=result.reply, kind=result.kind, data=result.data)
