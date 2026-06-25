"""Full-autonomy lifecycle (design → source → auto-approve → send → reply) + budget cap."""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.agents.main import design_campaign
from app.agents.outreach import run_conversation
from app.core.agent import CAMPAIGN_DAILY_TOKEN_BUDGET
from app.models import (
    AgentRole,
    AgentRun,
    Authorship,
    AutonomyLevel,
    AutonomyMode,
    Campaign,
    CampaignStatus,
    Enrollment,
    EnrollmentState,
)
from tests.factories import make_org, make_workspace
from tests.fake_llm import FakeLLM, text_turn, tool_turn


async def _enrollments(session: AsyncSession, campaign_id: str) -> list[Enrollment]:
    rows = await session.execute(select(Enrollment).where(Enrollment.campaign_id == campaign_id))
    return list(rows.scalars().all())


@pytest.mark.db
async def test_full_autonomy_lifecycle(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="e2e-full")
    ws = await make_workspace(db_session, org=org)
    now = datetime.now(UTC)
    c = Campaign(
        workspace_id=ws.id,
        name="Senior Backend",
        status=CampaignStatus.active,
        objective="Hire senior backend engineers",
        autonomy_level=AutonomyLevel.full,
        autonomy_mode=AutonomyMode.auto,
        authored_by=Authorship.agent,
        field_owners={"audience": "agent", "sequence": "agent"},
        criteria={},
        sequence=[],
        next_source_at=now - timedelta(minutes=1),
    )
    db_session.add(c)
    await db_session.flush()

    # 1 · DESIGN — the Main agent writes the agent-owned strategy
    design_llm = FakeLLM(
        [
            tool_turn("set_audience", {"titles": ["VP of Sales"]}, call_id="a1"),
            tool_turn(
                "set_sequence",
                {
                    "steps": [
                        {"channel": "email", "delay_days": 0, "subject": "Hi", "body": "Hello"}
                    ]
                },
                call_id="s1",
            ),
            text_turn("Designed."),
        ]
    )
    await design_campaign(db_session, llm=design_llm, campaign=c, organization_id=org.id)
    assert c.criteria.get("titles") == ["VP of Sales"]
    assert c.sequence

    # 2 · SOURCE — the Sourcing agent imports + (full autonomy) auto-approves the candidates
    source_llm = FakeLLM(
        [
            tool_turn("search", {"limit": 5}, call_id="se1"),
            tool_turn("import", {"ids": ["h0", "h1", "h2"]}, call_id="im1"),
            text_turn("Sourced."),
        ]
    )
    await worker.run_source_due(db_session, now=now, llm=source_llm)
    enrollments = await _enrollments(db_session, c.id)
    assert enrollments
    assert all(
        e.state == EnrollmentState.active for e in enrollments
    )  # candidate gate auto-approved

    # 3 · SEND — the deterministic engine ticks the active enrollments
    result = await worker.run_due(db_session, now=now)
    assert result["processed"] >= 1

    # 4 · REPLY — the Outreach agent hands off the warm thread
    conv_llm = FakeLLM(
        [tool_turn("hand_off", {"summary": "interested"}, call_id="h1"), text_turn("Done.")]
    )
    await run_conversation(
        db_session,
        llm=conv_llm,
        enrollment=enrollments[0],
        inbound_text="I'm interested — let's talk!",
        organization_id=org.id,
        now=now,
    )
    assert enrollments[0].state == EnrollmentState.handed_off


@pytest.mark.db
async def test_assisted_leaves_candidates_proposed(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="e2e-assist")
    ws = await make_workspace(db_session, org=org)
    now = datetime.now(UTC)
    c = Campaign(
        workspace_id=ws.id,
        name="C",
        status=CampaignStatus.active,
        autonomy_level=AutonomyLevel.assisted,
        criteria={"titles": ["VP of Sales"]},
        sequence=[],
        next_source_at=now - timedelta(minutes=1),
    )
    db_session.add(c)
    await db_session.flush()
    source_llm = FakeLLM(
        [
            tool_turn("search", {"limit": 4}, call_id="se1"),
            tool_turn("import", {"ids": ["h0", "h1"]}, call_id="im1"),
            text_turn("Sourced."),
        ]
    )
    await worker.run_source_due(db_session, now=now, llm=source_llm)
    enrollments = await _enrollments(db_session, c.id)
    assert enrollments
    assert all(
        e.state == EnrollmentState.proposed for e in enrollments
    )  # assisted: human gate holds


@pytest.mark.db
async def test_over_budget_falls_back_to_deterministic(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="e2e-budget")
    ws = await make_workspace(db_session, org=org)
    now = datetime.now(UTC)
    c = Campaign(
        workspace_id=ws.id,
        name="C",
        status=CampaignStatus.active,
        criteria={"titles": ["VP of Sales"]},
        sequence=[],
        next_source_at=now - timedelta(minutes=1),
    )
    db_session.add(c)
    await db_session.flush()
    # exhaust the campaign's daily token budget
    db_session.add(
        AgentRun(
            workspace_id=ws.id,
            campaign_id=c.id,
            role=AgentRole.sourcing,
            trigger="source_due",
            status="done",
            tokens=CAMPAIGN_DAILY_TOKEN_BUDGET,
        )
    )
    await db_session.flush()

    llm = FakeLLM([text_turn("unused")])
    res = await worker.run_source_due(db_session, now=now, llm=llm)
    assert res["sourced"] == 1
    assert llm.calls == 0  # over budget → deterministic; the agent never ran
    assert await _enrollments(db_session, c.id)  # deterministic still sourced
