"""Integration tests: the agent-native schema persists + defaults apply (Phase 1)."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    AgentRole,
    AgentRun,
    AgentStep,
    Authorship,
    AutonomyLevel,
    Campaign,
    Contact,
    Enrollment,
    EnrollmentState,
    Memory,
    MemoryScope,
    Organization,
    RelationshipStatus,
    Workspace,
    WorkspaceKind,
)


async def _org_ws(session: AsyncSession, slug: str) -> tuple[Organization, Workspace]:
    org = Organization(name="Ag", slug=slug, plan="demo")
    session.add(org)
    await session.flush()
    ws = Workspace(organization_id=org.id, name="W", kind=WorkspaceKind.team)
    session.add(ws)
    await session.flush()
    return org, ws


@pytest.mark.db
async def test_workspace_vertical_defaults_to_recruiting(db_session: AsyncSession) -> None:
    _org, ws = await _org_ws(db_session, "ag-vert")
    await db_session.refresh(ws)
    assert ws.vertical == "recruiting"


@pytest.mark.db
async def test_campaign_agent_fields_default(db_session: AsyncSession) -> None:
    _org, ws = await _org_ws(db_session, "ag-camp")
    c = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    db_session.add(c)
    await db_session.flush()
    await db_session.refresh(c)
    assert c.autonomy_level is AutonomyLevel.assisted
    assert c.authored_by is Authorship.human
    assert c.field_owners == {}
    assert c.constraints == {}
    assert c.brief_source == {}
    assert c.objective is None
    assert c.next_source_at is None


@pytest.mark.db
async def test_campaign_objective_and_provenance_roundtrip(db_session: AsyncSession) -> None:
    _org, ws = await _org_ws(db_session, "ag-obj")
    c = Campaign(
        workspace_id=ws.id,
        name="C",
        objective="Hire 2 senior backend engineers",
        authored_by=Authorship.agent,
        autonomy_level=AutonomyLevel.full,
        field_owners={"audience": "agent", "messaging": "human"},
        constraints={"send_cap_per_day": 50},
        brief_source={"origin": "upload", "raw_text": "JD..."},
        criteria={"titles": ["Backend Engineer"]},
        sequence=[],
    )
    db_session.add(c)
    await db_session.flush()
    await db_session.refresh(c)  # re-read from the DB to confirm round-trip
    assert c.objective == "Hire 2 senior backend engineers"
    assert c.authored_by is Authorship.agent
    assert c.autonomy_level is AutonomyLevel.full
    assert c.field_owners == {"audience": "agent", "messaging": "human"}
    assert c.constraints == {"send_cap_per_day": 50}
    assert c.brief_source["origin"] == "upload"


@pytest.mark.db
async def test_enrollment_journey_fields(db_session: AsyncSession) -> None:
    _org, ws = await _org_ws(db_session, "ag-enr")
    c = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(workspace_id=ws.id, full_name="Lee", skills=[], tags=[])
    db_session.add_all([c, contact])
    await db_session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=c.id,
        contact_id=contact.id,
        state=EnrollmentState.active,
        next_action={"kind": "linkedin_followup", "in_days": 2},
        signals={"opens": 2},
        park_until=datetime(2026, 1, 1, tzinfo=UTC),
    )
    db_session.add(enr)
    await db_session.flush()
    await db_session.refresh(enr)
    assert enr.relationship_status is RelationshipStatus.active  # default
    assert enr.next_action == {"kind": "linkedin_followup", "in_days": 2}
    assert enr.signals == {"opens": 2}
    assert enr.park_until is not None


@pytest.mark.db
async def test_contact_attributes_roundtrip(db_session: AsyncSession) -> None:
    _org, ws = await _org_ws(db_session, "ag-contact")
    contact = Contact(
        workspace_id=ws.id, full_name="Lee", skills=[], tags=[], attributes={"seniority": "senior"}
    )
    db_session.add(contact)
    await db_session.flush()
    await db_session.refresh(contact)
    assert contact.attributes == {"seniority": "senior"}


@pytest.mark.db
async def test_memory_keyed_recall_and_run_trace(db_session: AsyncSession) -> None:
    org, ws = await _org_ws(db_session, "ag-mem")
    c = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    db_session.add(c)
    await db_session.flush()

    mem = Memory(
        organization_id=org.id,
        scope=MemoryScope.campaign,
        scope_id=c.id,
        content="LinkedIn-first converts 3x for senior eng",
        meta={"confidence": "high"},
    )
    db_session.add(mem)
    await db_session.flush()
    await db_session.refresh(mem)
    assert mem.embedding is None  # keyed recall; vector seam empty

    found = (
        (
            await db_session.execute(
                select(Memory).where(
                    Memory.organization_id == org.id,
                    Memory.scope == MemoryScope.campaign,
                    Memory.scope_id == c.id,
                )
            )
        )
        .scalars()
        .all()
    )
    assert len(found) == 1

    run = AgentRun(
        workspace_id=ws.id, campaign_id=c.id, role=AgentRole.sourcing, trigger="source_due"
    )
    db_session.add(run)
    await db_session.flush()
    await db_session.refresh(run)
    assert run.status == "running"  # default
    step = AgentStep(run_id=run.id, seq=0, kind="tool_call", tool_name="search", content={"q": "x"})
    db_session.add(step)
    await db_session.flush()
    assert step.id


@pytest.mark.db
async def test_agent_run_cascade_deletes_steps(db_session: AsyncSession) -> None:
    _org, ws = await _org_ws(db_session, "ag-cascade")
    run = AgentRun(workspace_id=ws.id, role=AgentRole.main, trigger="cold_start")
    db_session.add(run)
    await db_session.flush()
    step = AgentStep(run_id=run.id, seq=0, kind="thought", content={})
    db_session.add(step)
    await db_session.flush()

    await db_session.delete(run)
    await db_session.flush()
    remaining = (
        (await db_session.execute(select(AgentStep).where(AgentStep.run_id == run.id)))
        .scalars()
        .all()
    )
    assert remaining == []
