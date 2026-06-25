"""The Main-agent chat (3rd trigger): a tool-using chat that returns text + typed entities.

Presentational tools (`show_funnel` / `show_candidates` / `preview_audience`) append typed entities
to the response; interactive ones carry a declarative `action`. See the entity catalog in
`.docs/agent-architecture.md` §12. Requires an LLM; the API layer returns an "unavailable" reply
when none is configured.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.prompts import DEFAULT_VERTICAL, compose_system
from app.core.db import new_id
from app.core.runtime import AgentLLM, Tool, run_episode, stream_episode
from app.core.types import JsonList, JsonObject
from app.models import AgentRole, Contact, Enrollment, Workspace
from app.services.cockpit.runs import campaign_funnel
from app.services.sourcing.contacts import list_contacts
from app.targeting import FIT_THRESHOLD, Targeting, evaluate

_CHAT_GUIDANCE = (
    "You are chatting with the operator about a campaign. To SHOW data, call show_funnel / "
    "show_candidates / preview_audience (don't describe it in prose). Keep replies short."
)


@dataclass
class ChatResult:
    reply: str
    entities: JsonList


@dataclass
class ChatContext:
    session: AsyncSession
    workspace_id: str
    organization_id: str
    campaign_id: str | None
    entities: JsonList = field(default_factory=list)


def _str(data: JsonObject, key: str) -> str | None:
    v = data.get(key)
    return v if isinstance(v, str) else None


def _strlist(data: JsonObject, key: str) -> list[str]:
    v = data.get(key)
    return [str(x) for x in v] if isinstance(v, list) else []


def _targeting(data: JsonObject) -> Targeting:
    return Targeting(
        titles=_strlist(data, "titles"),
        skills=_strlist(data, "skills"),
        locations=_strlist(data, "locations"),
        keywords=_str(data, "keywords"),
    )


def chat_tools(ctx: ChatContext) -> list[Tool]:
    """Presentational tools — each appends a typed entity (catalog §12) and returns a short ack."""

    def _emit(kind: str, data: JsonObject, action: JsonObject | None = None) -> None:
        entity: JsonObject = {"type": kind, "id": new_id(), "data": data}
        if action is not None:
            entity["action"] = action
        ctx.entities.append(entity)

    async def show_funnel(data: JsonObject) -> JsonObject:
        if ctx.campaign_id is None:
            return {"error": "no campaign in context"}
        f = await campaign_funnel(ctx.session, campaign_id=ctx.campaign_id)
        _emit(
            "funnel",
            {
                "sourced": f.sourced,
                "contacted": f.contacted,
                "replied": f.replied,
                "handed_off": f.handed_off,
            },
        )
        return {"shown": "funnel"}

    async def show_candidates(data: JsonObject) -> JsonObject:
        if ctx.campaign_id is None:
            return {"error": "no campaign in context"}
        rows = await ctx.session.execute(
            select(Enrollment, Contact)
            .join(Contact, Enrollment.contact_id == Contact.id)
            .where(Enrollment.campaign_id == ctx.campaign_id)
            .limit(10)
        )
        candidates: JsonList = [
            {
                "id": c.id,
                "name": c.full_name,
                "title": c.title,
                "company": c.company,
                "score": e.score,
                "status": e.state.value,
            }
            for e, c in rows.tuples().all()
        ]
        _emit("candidate_list", {"candidates": candidates, "total": len(candidates)})
        return {"shown": len(candidates)}

    async def preview_audience(data: JsonObject) -> JsonObject:
        targeting = _targeting(data)
        contacts = await list_contacts(ctx.session, workspace_id=ctx.workspace_id)
        sample: JsonList = []
        estimate = 0
        for c in contacts:
            score, _ = evaluate(c, targeting)
            if score >= FIT_THRESHOLD:
                estimate += 1
                if len(sample) < 5:
                    sample.append(
                        {"id": c.id, "name": c.full_name, "title": c.title, "score": score}
                    )
        action: JsonObject = {
            "verb": "apply",
            "endpoint": "/agent/apply-audience",
            "params": {"campaign_id": ctx.campaign_id, "criteria": targeting.model_dump()},
        }
        _emit(
            "audience_preview",
            {"criteria": targeting.model_dump(), "estimate": estimate, "sample": sample},
            action,
        )
        return {"estimate": estimate}

    obj = "object"
    arr = {"type": "array", "items": {"type": "string"}}
    return [
        Tool("show_funnel", "Show the campaign's funnel.", {"type": obj}, show_funnel),
        Tool("show_candidates", "Show candidates in the campaign.", {"type": obj}, show_candidates),
        Tool(
            "preview_audience",
            "Preview a targeting spec: estimate + sample (with an Apply action).",
            {"type": obj, "properties": {"titles": arr, "skills": arr, "locations": arr}},
            preview_audience,
        ),
    ]


async def run_chat(
    session: AsyncSession,
    *,
    llm: AgentLLM,
    workspace_id: str,
    organization_id: str,
    message: str,
    campaign_id: str | None = None,
) -> ChatResult:
    """Run one Main-agent chat turn; returns the narration + the typed entities it surfaced."""
    workspace = await session.get(Workspace, workspace_id)
    vertical = workspace.vertical if workspace else DEFAULT_VERTICAL
    ctx = ChatContext(
        session=session,
        workspace_id=workspace_id,
        organization_id=organization_id,
        campaign_id=campaign_id,
    )
    result = await run_episode(
        session,
        llm=llm,
        role=AgentRole.main,
        trigger="chat",
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        system=compose_system(AgentRole.main, vertical, context=_CHAT_GUIDANCE),
        user_prompt=message,
        tools=chat_tools(ctx),
    )
    return ChatResult(reply=result.text, entities=ctx.entities)


async def run_chat_stream(
    session: AsyncSession,
    *,
    llm: AgentLLM,
    workspace_id: str,
    organization_id: str,
    message: str,
    campaign_id: str | None = None,
) -> AsyncIterator[JsonObject]:
    """Streaming Main-agent chat turn: yields `{"type":"token"}` events as the narration streams,
    then a final `{"type":"done", "entities": [...]}` with the typed UI blocks the tools surfaced.
    """
    workspace = await session.get(Workspace, workspace_id)
    vertical = workspace.vertical if workspace else DEFAULT_VERTICAL
    ctx = ChatContext(
        session=session,
        workspace_id=workspace_id,
        organization_id=organization_id,
        campaign_id=campaign_id,
    )
    async for ev in stream_episode(
        session,
        llm=llm,
        role=AgentRole.main,
        trigger="chat",
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        system=compose_system(AgentRole.main, vertical, context=_CHAT_GUIDANCE),
        user_prompt=message,
        tools=chat_tools(ctx),
    ):
        yield ev
    yield {"type": "done", "entities": ctx.entities}
