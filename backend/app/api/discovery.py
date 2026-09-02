"""People discovery (Rail B): live provider search + import into the workspace contacts table.

Search/enrich never touch the DB (pass-through); import is the only write. With no provider key
configured (platform or BYO) the provider set is empty and search returns no results.
"""

from collections import Counter
from collections.abc import Sequence

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.core import llm
from app.ext.base import PersonHit, SourceProvider
from app.ext.registry import build_providers_for_org
from app.models import ConnectionProvider
from app.services.sourcing import discovery, usage
from app.services.workspace.connections import user_seat
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
        for p in await _providers(session, ctx)
    ]


async def _providers(
    session: SessionDep, ctx: ContextDep, *, selection: list[str] | None = None
) -> Sequence[SourceProvider]:
    """The provider set for this caller, acting as *their* connected LinkedIn seat.

    LinkedIn search runs as a member, so it needs an account to search from. That used to come
    only from `UNIPILE_ACCOUNT_ID`, which meant search was dead for everyone until a deployment
    set one global account — and the error told you to connect a seat in Settings, which did
    nothing, because nothing on this path ever read a `Connection`. Their own seat, never a
    colleague's: it is that person's LinkedIn account and their rate limits being spent.
    """
    seat = await user_seat(session, user_id=ctx.user_id, provider=ConnectionProvider.linkedin)
    return await build_providers_for_org(
        session,
        ctx.org_id,
        selection=selection,
        linkedin_account_id=seat.external_id if seat else None,
    )


class PeopleSearchIn(Targeting):
    limit: int = 25
    # restrict to these provider keys (empty = all enabled)
    providers: list[str] = Field(default_factory=list)


class ProviderFailureOut(BaseModel):
    provider: str
    message: str


class PeopleSearchOut(BaseModel):
    results: list[PersonHit]
    providers: list[str]
    # Providers that couldn't answer. Empty `results` with a failure here is a broken search, not
    # an empty one — the client must be able to tell those apart.
    errors: list[ProviderFailureOut] = []


@router.post("/search", response_model=PeopleSearchOut)
async def search_people(
    body: PeopleSearchIn, ctx: ContextDep, session: SessionDep
) -> PeopleSearchOut:
    require_workspace(ctx)
    providers = await _providers(session, ctx)
    if body.providers:
        providers = [p for p in providers if p.key in body.providers]
    outcome = await discovery.search_people(providers, body, limit=body.limit)
    failed = {f.provider for f in outcome.failures}
    used = [p.key for p in providers if p.capabilities.search and p.key not in failed]
    for provider_key in used:
        await usage.record(
            session, organization_id=ctx.org_id, provider=provider_key, kind="search"
        )
    return PeopleSearchOut(
        results=outcome.hits,
        providers=used,
        errors=[
            ProviderFailureOut(provider=f.provider, message=f.message) for f in outcome.failures
        ],
    )


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
    providers = await _providers(session, ctx)
    hits = await discovery.verify_hits(providers, body.hits)
    created = await discovery.import_hits(session, workspace_id=ws, hits=hits)
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
    providers = await _providers(session, ctx)
    hit = await discovery.enrich_ref(
        providers,
        email=body.email,
        linkedin_url=body.linkedin_url,
        name=body.name,
        company=body.company,
    )
    return EnrichOut(hit=hit)
