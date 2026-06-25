"""The Main agent: design + continuously optimize a campaign's strategy (provenance-aware).

Cold-start = design from the brief; scheduled review = improve the weakest funnel stage. The `set_*`
tools write only agent-owned sections; a human-owned section gets a suggestion (an audit event the
notifications feed surfaces) instead of being overwritten.
"""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.intake import parse_brief
from app.agents.provenance import is_agent_owned
from app.agents.verticals import DEFAULT_VERTICAL, compose_system
from app.core.agent import AgentLLM, AgentResult, Tool, run_episode
from app.core.types import JsonList, JsonObject
from app.models import AgentRole, AuditEvent, Campaign, Workspace
from app.services.agent.runs import campaign_funnel
from app.services.sourcing.contacts import list_contacts
from app.targeting import FIT_THRESHOLD, Targeting, as_targeting, evaluate

_DEFAULT_SEQUENCE: JsonList = [
    {"channel": "linkedin", "delay_days": 0},
    {"channel": "email", "delay_days": 3},
    {"channel": "linkedin", "delay_days": 7},
]


@dataclass
class MainContext:
    session: AsyncSession
    campaign: Campaign
    organization_id: str


def _str(data: JsonObject, key: str) -> str | None:
    v = data.get(key)
    return v if isinstance(v, str) else None


def _strlist(data: JsonObject, key: str) -> list[str]:
    v = data.get(key)
    return [str(x) for x in v] if isinstance(v, list) else []


def _build_targeting(data: JsonObject) -> Targeting:
    return Targeting(
        titles=_strlist(data, "titles"),
        skills=_strlist(data, "skills"),
        locations=_strlist(data, "locations"),
        seniorities=_strlist(data, "seniorities"),
        industries=_strlist(data, "industries"),
        keywords=_str(data, "keywords"),
    )


def _json_list(v: object) -> JsonList:
    if not isinstance(v, list):
        return []
    return [{str(k): val for k, val in s.items()} for s in v if isinstance(s, dict)]


def _audit(ctx: MainContext, *, action: str, summary: str) -> None:
    ctx.session.add(
        AuditEvent(
            organization_id=ctx.organization_id,
            workspace_id=ctx.campaign.workspace_id,
            actor_user_id=None,
            action=action,
            target_type="campaign",
            target_id=ctx.campaign.id,
            summary=summary[:500],
        )
    )


def main_tools(ctx: MainContext) -> list[Tool]:
    """The Main agent's provenance-aware strategy toolset, bound to one campaign."""

    async def estimate_audience(data: JsonObject) -> JsonObject:
        targeting = _build_targeting(data)
        if not any([targeting.titles, targeting.skills, targeting.locations, targeting.keywords]):
            targeting = as_targeting(ctx.campaign.criteria)
        contacts = await list_contacts(ctx.session, workspace_id=ctx.campaign.workspace_id)
        n = sum(1 for c in contacts if evaluate(c, targeting)[0] >= FIT_THRESHOLD)
        return {"estimate": n}

    async def set_audience(data: JsonObject) -> JsonObject:
        if not is_agent_owned(ctx.campaign, "audience"):
            _audit(ctx, action="agent.suggestion", summary=f"audience: {_str(data, 'rationale')}")
            return {"applied": False, "reason": "audience is human-owned — recorded a suggestion"}
        ctx.campaign.criteria = _build_targeting(data).model_dump()
        return {"applied": True}

    async def set_sequence(data: JsonObject) -> JsonObject:
        if not is_agent_owned(ctx.campaign, "sequence"):
            _audit(ctx, action="agent.suggestion", summary=f"sequence: {_str(data, 'rationale')}")
            return {"applied": False, "reason": "sequence is human-owned — recorded a suggestion"}
        ctx.campaign.sequence = _json_list(data.get("steps"))
        return {"applied": True}

    async def read_funnel(data: JsonObject) -> JsonObject:
        f = await campaign_funnel(ctx.session, campaign_id=ctx.campaign.id)
        return {
            "sourced": f.sourced,
            "contacted": f.contacted,
            "replied": f.replied,
            "handed_off": f.handed_off,
        }

    async def flag(data: JsonObject) -> JsonObject:
        _audit(ctx, action="agent.flag", summary=_str(data, "note") or "")
        return {"flagged": True}

    arr = {"type": "array", "items": {"type": "string"}}
    obj = "object"
    return [
        Tool(
            "estimate_audience",
            "Estimate how many workspace contacts match a targeting spec.",
            {"type": obj, "properties": {"titles": arr, "skills": arr, "locations": arr}},
            estimate_audience,
        ),
        Tool(
            "set_audience",
            "Set the campaign's audience/criteria (applies only if the agent owns this section).",
            {"type": obj, "properties": {"titles": arr, "skills": arr, "locations": arr}},
            set_audience,
        ),
        Tool(
            "set_sequence",
            "Set the campaign's outreach sequence (applies only if the agent owns this section).",
            {"type": obj, "properties": {"steps": {"type": "array"}}, "required": ["steps"]},
            set_sequence,
        ),
        Tool(
            "read_funnel",
            "Read the campaign's current funnel counts.",
            {"type": obj, "properties": {}},
            read_funnel,
        ),
        Tool(
            "flag",
            "Flag the human recruiter with a note (e.g. when you'd pause the campaign).",
            {"type": obj, "properties": {"note": {"type": "string"}}},
            flag,
        ),
    ]


async def _ctx_for(
    session: AsyncSession, campaign: Campaign, organization_id: str
) -> tuple[MainContext, str]:
    workspace = await session.get(Workspace, campaign.workspace_id)
    vertical = workspace.vertical if workspace else DEFAULT_VERTICAL
    return MainContext(
        session=session, campaign=campaign, organization_id=organization_id
    ), vertical


async def design_campaign(
    session: AsyncSession, *, llm: AgentLLM, campaign: Campaign, organization_id: str
) -> AgentResult:
    """Cold-start: design the campaign strategy from its brief."""
    ctx, vertical = await _ctx_for(session, campaign, organization_id)
    user = (
        f"Design the campaign strategy from this brief.\n"
        f"Objective: {campaign.objective or campaign.name}\n"
        f"Current criteria: {campaign.criteria}\n"
        "Sanity-check with estimate_audience, then set the audience and sequence. Only sections "
        "you own apply; others become suggestions."
    )
    return await run_episode(
        session,
        llm=llm,
        role=AgentRole.main,
        trigger="cold_start",
        workspace_id=campaign.workspace_id,
        campaign_id=campaign.id,
        system=compose_system(AgentRole.main, vertical),
        user_prompt=user,
        tools=main_tools(ctx),
    )


async def review_campaign(
    session: AsyncSession, *, llm: AgentLLM, campaign: Campaign, organization_id: str
) -> AgentResult:
    """Scheduled review: improve the weakest funnel stage with the smallest change."""
    ctx, vertical = await _ctx_for(session, campaign, organization_id)
    user = (
        "Review the funnel and improve the weakest stage with the smallest change.\n"
        f"Objective: {campaign.objective or campaign.name}\n"
        "Read the funnel, then adjust the audience or sequence (agent-owned), or flag the human."
    )
    return await run_episode(
        session,
        llm=llm,
        role=AgentRole.main,
        trigger="review",
        workspace_id=campaign.workspace_id,
        campaign_id=campaign.id,
        system=compose_system(AgentRole.main, vertical),
        user_prompt=user,
        tools=main_tools(ctx),
    )


async def deterministic_design(
    session: AsyncSession, *, campaign: Campaign, organization_id: str
) -> None:
    """LLM-free design: objective -> criteria + a default sequence (agent-owned sections only)."""
    _ctx, vertical = await _ctx_for(session, campaign, organization_id)
    brief = await parse_brief(campaign.objective or campaign.name, vertical=vertical)
    if is_agent_owned(campaign, "audience"):
        campaign.criteria = brief.targeting.model_dump()
    if is_agent_owned(campaign, "sequence") and not campaign.sequence:
        campaign.sequence = list(_DEFAULT_SEQUENCE)
