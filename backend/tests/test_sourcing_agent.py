"""Phase 3: the Sourcing agent + its tools (against the demo provider + a scripted LLM)."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sourcing import SourcingContext, run_sourcing, sourcing_tools
from app.core.agent import Tool
from app.ext.registry import build_providers_for_org
from app.models import (
    Campaign,
    CampaignStatus,
    Contact,
    Enrollment,
    EnrollmentState,
    Organization,
    Workspace,
)
from app.targeting import as_targeting
from tests.factories import make_org, make_workspace
from tests.fake_llm import FakeLLM, text_turn, tool_turn


async def _campaign(
    session: AsyncSession, *, slug: str
) -> tuple[Organization, Workspace, Campaign]:
    org = await make_org(session, slug=slug)
    ws = await make_workspace(session, org=org)
    c = Campaign(
        workspace_id=ws.id,
        name="Senior Backend",
        status=CampaignStatus.active,
        criteria={"titles": ["VP of Sales"], "skills": ["Salesforce"]},
        sequence=[],
    )
    session.add(c)
    await session.flush()
    return org, ws, c


async def _ctx(
    session: AsyncSession, org: Organization, ws: Workspace, c: Campaign
) -> SourcingContext:
    providers = await build_providers_for_org(session, org.id)
    return SourcingContext(
        session=session,
        workspace_id=ws.id,
        organization_id=org.id,
        campaign=c,
        providers=providers,
        targeting=as_targeting(c.criteria),
    )


def _tools(ctx: SourcingContext) -> dict[str, Tool]:
    return {t.name: t for t in sourcing_tools(ctx)}


@pytest.mark.db
async def test_search_tool_finds_demo_hits(db_session: AsyncSession) -> None:
    org, ws, c = await _campaign(db_session, slug="src-search")
    tools = _tools(await _ctx(db_session, org, ws, c))
    res = await tools["search"].run({"limit": 8})
    assert res["found"] == 8  # the demo provider returns the requested count
    sample = res["sample"]
    assert isinstance(sample, list) and sample


@pytest.mark.db
async def test_import_tool_creates_contacts_and_enrollments(db_session: AsyncSession) -> None:
    org, ws, c = await _campaign(db_session, slug="src-import")
    ctx = await _ctx(db_session, org, ws, c)
    tools = _tools(ctx)
    await tools["search"].run({"limit": 6})
    ids = list(ctx.hits.keys())[:4]
    res = await tools["import"].run({"ids": ids})
    assert res["imported"] == 4
    enrolled = res["enrolled"]
    assert isinstance(enrolled, int) and enrolled >= 1  # qualifying demo hits → proposed

    contacts = (
        (await db_session.execute(select(Contact).where(Contact.workspace_id == ws.id)))
        .scalars()
        .all()
    )
    assert len(contacts) == 4
    enr = (
        (await db_session.execute(select(Enrollment).where(Enrollment.campaign_id == c.id)))
        .scalars()
        .all()
    )
    assert enr and all(e.state == EnrollmentState.proposed for e in enr)


@pytest.mark.db
async def test_check_suppressed_tool(db_session: AsyncSession) -> None:
    org, ws, c = await _campaign(db_session, slug="src-supp")
    tools = _tools(await _ctx(db_session, org, ws, c))
    res = await tools["check_suppressed"].run({"email": "nobody@example.com"})
    assert res["suppressed"] is False


@pytest.mark.db
async def test_sourcing_agent_end_to_end(db_session: AsyncSession) -> None:
    org, _ws, c = await _campaign(db_session, slug="src-e2e")
    # Scripted LLM: search (populates h0..h5) → import the first four → finish.
    llm = FakeLLM(
        [
            tool_turn("search", {"limit": 6}, call_id="s1"),
            tool_turn("import", {"ids": ["h0", "h1", "h2", "h3"]}, call_id="i1"),
            text_turn("Sourced and enrolled 4 candidates."),
        ]
    )
    res = await run_sourcing(db_session, llm=llm, campaign=c, organization_id=org.id)
    assert res.status == "done"

    enr = (
        (await db_session.execute(select(Enrollment).where(Enrollment.campaign_id == c.id)))
        .scalars()
        .all()
    )
    assert len(enr) >= 1
    assert all(e.state == EnrollmentState.proposed for e in enr)
