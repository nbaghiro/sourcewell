"""People discovery (Rail B): live provider search + import into the workspace contacts table.

Search/enrich never touch the DB (pass-through); import is the only write. Works with zero keys
(falls back to the demo provider), so the flow is exercisable in every environment.
"""

from collections import Counter

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.core import llm
from app.deps import ContextDep, SessionDep, require_workspace
from app.services.sourcing import people, usage
from app.services.sourcing.adapters.base import PersonHit
from app.services.sourcing.adapters.registry import build_providers_for_org
from app.targeting import Targeting

router = APIRouter(prefix="/people", tags=["people"])


class ProviderOut(BaseModel):
    key: str
    name: str
    search: bool
    enrich: bool
    verify_email: bool


@router.get("/providers", response_model=list[ProviderOut])
async def list_providers(ctx: ContextDep, session: SessionDep) -> list[ProviderOut]:
    return [
        ProviderOut(
            key=p.key,
            name=p.name,
            search=p.capabilities.search,
            enrich=p.capabilities.enrich,
            verify_email=p.capabilities.verify_email,
        )
        for p in await build_providers_for_org(session, ctx.org_id)
    ]


class PeopleSearchIn(Targeting):
    limit: int = 25
    # restrict to these provider keys (empty = all enabled)
    providers: list[str] = Field(default_factory=list)


class PeopleSearchOut(BaseModel):
    results: list[PersonHit]
    providers: list[str]


@router.post("/search", response_model=PeopleSearchOut)
async def search_people(
    body: PeopleSearchIn, ctx: ContextDep, session: SessionDep
) -> PeopleSearchOut:
    require_workspace(ctx)
    providers = await build_providers_for_org(session, ctx.org_id)
    if body.providers:
        providers = [p for p in providers if p.key in body.providers]
    results = await people.search_people(providers, body, limit=body.limit)
    used = [p.key for p in providers if p.capabilities.search]
    for provider_key in used:
        await usage.record(
            session, organization_id=ctx.org_id, provider=provider_key, kind="search"
        )
    return PeopleSearchOut(results=results, providers=used)


class ParseIn(BaseModel):
    text: str


class ParseOut(BaseModel):
    titles: list[str]
    skills: list[str]
    locations: list[str]
    keywords: str


@router.post("/parse", response_model=ParseOut)
async def parse_query(body: ParseIn, ctx: ContextDep) -> ParseOut:
    """Natural language -> search criteria (Claude when enabled, else the text as keywords)."""
    fallback = ParseOut(titles=[], skills=[], locations=[], keywords=body.text.strip())
    if not llm.is_enabled() or not body.text.strip():
        return fallback
    system = "Extract B2B people-search filters from a recruiter/sales request."
    user = (
        f"Request: {body.text!r}\n"
        'Return JSON {"titles": [job titles], "skills": [skills/keywords], '
        '"locations": [places; use "EU"/"US" for regions], "keywords": leftover free text}.'
    )
    obj = await llm.complete_json(system, user, max_tokens=220)
    if not obj:
        return fallback

    def _as_list(v: object) -> list[str]:
        return [str(x) for x in v if str(x).strip()] if isinstance(v, list) else []

    return ParseOut(
        titles=_as_list(obj.get("titles")),
        skills=_as_list(obj.get("skills")),
        locations=_as_list(obj.get("locations")),
        keywords=str(obj.get("keywords") or ""),
    )


class ImportIn(BaseModel):
    hits: list[PersonHit]


class ImportOut(BaseModel):
    imported: int
    contact_ids: list[str]


@router.post("/import", response_model=ImportOut)
async def import_people(body: ImportIn, ctx: ContextDep, session: SessionDep) -> ImportOut:
    ws = require_workspace(ctx)
    providers = await build_providers_for_org(session, ctx.org_id)
    hits = await people.verify_hits(providers, body.hits)
    created = await people.import_hits(session, workspace_id=ws, hits=hits)
    for provider_key, n in Counter(c.source for c in created).items():
        await usage.record(
            session, organization_id=ctx.org_id, provider=provider_key, kind="import", count=n
        )
    return ImportOut(imported=len(created), contact_ids=[c.id for c in created])


class UsageOut(BaseModel):
    provider: str
    kind: str
    day: str
    count: int


@router.get("/usage", response_model=list[UsageOut])
async def list_usage(ctx: ContextDep, session: SessionDep) -> list[UsageOut]:
    return [
        UsageOut(
            provider=str(row["provider"]),
            kind=str(row["kind"]),
            day=str(row["day"]),
            count=int(row["count"]) if isinstance(row["count"], int) else 0,
        )
        for row in await usage.summary(session, ctx.org_id)
    ]


class EnrichIn(BaseModel):
    email: str | None = None
    linkedin_url: str | None = None
    name: str | None = None
    company: str | None = None


class EnrichOut(BaseModel):
    hit: PersonHit | None


@router.post("/enrich", response_model=EnrichOut)
async def enrich_person(body: EnrichIn, ctx: ContextDep, session: SessionDep) -> EnrichOut:
    require_workspace(ctx)
    providers = await build_providers_for_org(session, ctx.org_id)
    hit = await people.enrich_ref(
        providers,
        email=body.email,
        linkedin_url=body.linkedin_url,
        name=body.name,
        company=body.company,
    )
    return EnrichOut(hit=hit)
