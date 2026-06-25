"""The Sourcing agent: find + qualify candidates for a campaign via a bounded tool-use episode.

The tools wrap the existing deterministic primitives (discovery / ext providers / targeting /
suppression); the agent only decides what to search, enrich, and import. The work is deterministic
and already tested — the model just orchestrates it.
"""

from collections.abc import Sequence
from dataclasses import dataclass, field

from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.verticals import DEFAULT_VERTICAL, compose_system
from app.core.agent import AgentLLM, AgentResult, Tool, run_episode
from app.core.types import JsonList, JsonObject
from app.ext.base import PersonHit, SourceProvider
from app.ext.registry import build_providers_for_org
from app.models import AgentRole, Campaign, Workspace
from app.services.sourcing.contacts import list_contacts
from app.services.sourcing.discovery import enrich_ref, import_hits, search_people
from app.services.sourcing.ranking import rank_campaign
from app.services.sourcing.suppression import is_suppressed
from app.targeting import FIT_THRESHOLD, Targeting, as_targeting, evaluate


@dataclass
class SourcingContext:
    """Shared state for one sourcing episode — the session, scope, and the working set of hits."""

    session: AsyncSession
    workspace_id: str
    organization_id: str
    campaign: Campaign
    providers: Sequence[SourceProvider]
    targeting: Targeting
    hits: dict[str, PersonHit] = field(default_factory=dict)


def _str(data: JsonObject, key: str) -> str | None:
    v = data.get(key)
    return v if isinstance(v, str) else None


def _int(data: JsonObject, key: str, default: int) -> int:
    v = data.get(key)
    if isinstance(v, bool):
        return default
    if isinstance(v, int):
        return v
    if isinstance(v, float):
        return int(v)
    return default


def _ids(data: JsonObject) -> list[str]:
    v = data.get("ids")
    return [str(x) for x in v] if isinstance(v, list) else []


def sourcing_tools(ctx: SourcingContext) -> list[Tool]:
    """The Sourcing agent's toolset, bound to one episode's context."""

    async def search(data: JsonObject) -> JsonObject:
        limit = min(_int(data, "limit", 25), 50)
        hits = await search_people(ctx.providers, ctx.targeting, limit=limit, use_cache=False)
        ctx.hits = {f"h{i}": h for i, h in enumerate(hits)}
        sample: JsonList = [
            {
                "id": hid,
                "name": h.full_name,
                "title": h.title,
                "company": h.company,
                "score": h.score,
                "has_email": bool(h.email),
            }
            for hid, h in list(ctx.hits.items())[:10]
        ]
        return {"found": len(hits), "sample": sample}

    async def enrich(data: JsonObject) -> JsonObject:
        hit = ctx.hits.get(_str(data, "id") or "")
        if hit is None:
            return {"error": "unknown hit id"}
        found = await enrich_ref(
            ctx.providers,
            email=hit.email,
            linkedin_url=hit.linkedin_url,
            name=hit.full_name,
            company=hit.company,
        )
        if found is None:
            return {"enriched": False}
        hit.email = hit.email or found.email
        hit.linkedin_url = hit.linkedin_url or found.linkedin_url
        return {"enriched": True, "has_email": bool(hit.email)}

    async def score(data: JsonObject) -> JsonObject:
        hit = ctx.hits.get(_str(data, "id") or "")
        if hit is None:
            return {"error": "unknown hit id"}
        s, rationale = evaluate(hit, ctx.targeting)
        return {"score": s, "rationale": rationale, "qualifies": s >= FIT_THRESHOLD}

    async def check_suppressed(data: JsonObject) -> JsonObject:
        supp = await is_suppressed(
            ctx.session, organization_id=ctx.organization_id, email=_str(data, "email")
        )
        return {"suppressed": supp}

    async def list_existing(data: JsonObject) -> JsonObject:
        contacts = await list_contacts(ctx.session, workspace_id=ctx.workspace_id)
        return {"count": len(contacts)}

    async def import_(data: JsonObject) -> JsonObject:
        chosen = [ctx.hits[i] for i in _ids(data) if i in ctx.hits]
        kept: list[PersonHit] = []
        for h in chosen:
            if h.email and await is_suppressed(
                ctx.session, organization_id=ctx.organization_id, email=h.email
            ):
                continue
            kept.append(h)
        contacts = await import_hits(ctx.session, workspace_id=ctx.workspace_id, hits=kept)
        enrollments = await rank_campaign(
            ctx.session, workspace_id=ctx.workspace_id, campaign=ctx.campaign
        )
        return {"imported": len(contacts), "enrolled": len(enrollments)}

    obj = "object"
    return [
        Tool(
            "search",
            "Search providers for candidates matching the campaign criteria.",
            {"type": obj, "properties": {"limit": {"type": "integer"}}},
            search,
        ),
        Tool(
            "enrich",
            "Recover contact details (email/LinkedIn) for a found candidate by id.",
            {"type": obj, "properties": {"id": {"type": "string"}}, "required": ["id"]},
            enrich,
        ),
        Tool(
            "score",
            "Score a found candidate's fit against the criteria.",
            {"type": obj, "properties": {"id": {"type": "string"}}, "required": ["id"]},
            score,
        ),
        Tool(
            "check_suppressed",
            "Check whether an email is on the do-not-contact list.",
            {"type": obj, "properties": {"email": {"type": "string"}}, "required": ["email"]},
            check_suppressed,
        ),
        Tool(
            "list_existing",
            "Count the contacts already in the workspace (dedup awareness).",
            {"type": obj, "properties": {}},
            list_existing,
        ),
        Tool(
            "import",
            "Import candidates by id as contacts and enroll the qualifying ones.",
            {
                "type": obj,
                "properties": {"ids": {"type": "array", "items": {"type": "string"}}},
                "required": ["ids"],
            },
            import_,
        ),
    ]


async def run_sourcing(
    session: AsyncSession, *, llm: AgentLLM, campaign: Campaign, organization_id: str
) -> AgentResult:
    """Run one Sourcing episode for an active campaign."""
    workspace = await session.get(Workspace, campaign.workspace_id)
    vertical = workspace.vertical if workspace else DEFAULT_VERTICAL
    providers = await build_providers_for_org(session, organization_id)
    targeting = as_targeting(campaign.criteria)
    ctx = SourcingContext(
        session=session,
        workspace_id=campaign.workspace_id,
        organization_id=organization_id,
        campaign=campaign,
        providers=providers,
        targeting=targeting,
    )
    system = compose_system(AgentRole.sourcing, vertical)
    user = (
        f"Goal: {campaign.objective or campaign.name}\n"
        f"Criteria: {targeting.model_dump_json()}\n"
        "Find, qualify, and import strong candidates into the campaign. Stop once you've added a "
        "good batch or exhausted strong matches."
    )
    return await run_episode(
        session,
        llm=llm,
        role=AgentRole.sourcing,
        trigger="source_due",
        workspace_id=campaign.workspace_id,
        campaign_id=campaign.id,
        system=system,
        user_prompt=user,
        tools=sourcing_tools(ctx),
    )


async def deterministic_source(
    session: AsyncSession, *, campaign: Campaign, organization_id: str
) -> int:
    """LLM-free sourcing fallback: search providers, import, and rank into proposed enrollments."""
    providers = await build_providers_for_org(session, organization_id)
    targeting = as_targeting(campaign.criteria)
    hits = await search_people(providers, targeting, limit=25, use_cache=False)
    kept: list[PersonHit] = []
    for h in hits:
        if h.email and await is_suppressed(session, organization_id=organization_id, email=h.email):
            continue
        kept.append(h)
    await import_hits(session, workspace_id=campaign.workspace_id, hits=kept)
    enrollments = await rank_campaign(
        session, workspace_id=campaign.workspace_id, campaign=campaign
    )
    return len(enrollments)
