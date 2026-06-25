"""Phase 5: the Main agent — brief intake, cold-start design, review, provenance enforcement."""

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.intake import parse_brief
from app.agents.main import design_campaign, deterministic_design, review_campaign
from app.models import AuditEvent, Authorship, Campaign
from tests.factories import make_org, make_workspace
from tests.fake_llm import FakeLLM, text_turn, tool_turn


def _agent_campaign(ws_id: str, *, objective: str, owners: dict[str, str]) -> Campaign:
    return Campaign(
        workspace_id=ws_id,
        name="C",
        objective=objective,
        authored_by=Authorship.agent,
        field_owners=owners,
        criteria={},
        sequence=[],
    )


async def _setup(client: AsyncClient, slug: str) -> dict[str, str]:
    signup = await client.post(
        "/organizations",
        json={
            "org_name": f"Org {slug}",
            "slug": slug,
            "admin_email": f"admin@{slug}.com",
            "admin_name": "Admin",
        },
    )
    assert signup.status_code == 201
    uid = signup.json()["admin_user_id"]
    ws = await client.post(
        "/workspaces", json={"name": "Team", "kind": "team"}, headers={"X-User-Id": uid}
    )
    assert ws.status_code == 201
    return {"X-User-Id": uid, "X-Workspace-Id": ws.json()["id"]}


# --- parse_brief -------------------------------------------------------------


async def test_parse_brief_keyword_fallback() -> None:
    res = await parse_brief("Hire a senior backend engineer in NYC")  # no LLM in tests
    assert "senior backend" in res.objective.lower()
    assert res.targeting.keywords  # the keyword fallback


# --- the Main agent ----------------------------------------------------------


@pytest.mark.db
async def test_design_campaign_writes_strategy(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="main-design")
    ws = await make_workspace(db_session, org=org)
    c = _agent_campaign(
        ws.id, objective="Hire backend eng", owners={"audience": "agent", "sequence": "agent"}
    )
    db_session.add(c)
    await db_session.flush()
    llm = FakeLLM(
        [
            tool_turn(
                "set_audience", {"titles": ["Backend Engineer"], "skills": ["Python"]}, call_id="a1"
            ),
            tool_turn(
                "set_sequence", {"steps": [{"channel": "email", "delay_days": 0}]}, call_id="s1"
            ),
            text_turn("Designed."),
        ]
    )
    res = await design_campaign(db_session, llm=llm, campaign=c, organization_id=org.id)
    assert res.status == "done"
    assert c.criteria.get("titles") == ["Backend Engineer"]
    assert len(c.sequence) == 1


@pytest.mark.db
async def test_design_respects_pinned_audience(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="main-pin")
    ws = await make_workspace(db_session, org=org)
    c = _agent_campaign(ws.id, objective="x", owners={"audience": "human", "sequence": "agent"})
    c.criteria = {"titles": ["Original"]}
    db_session.add(c)
    await db_session.flush()
    llm = FakeLLM(
        [
            tool_turn(
                "set_audience", {"titles": ["Changed"], "rationale": "broaden"}, call_id="a1"
            ),
            text_turn("done"),
        ]
    )
    await design_campaign(db_session, llm=llm, campaign=c, organization_id=org.id)
    assert c.criteria == {"titles": ["Original"]}  # pinned section not overwritten
    suggestions = (
        (
            await db_session.execute(
                select(AuditEvent).where(AuditEvent.action == "agent.suggestion")
            )
        )
        .scalars()
        .all()
    )
    assert suggestions  # the blocked write became a suggestion


@pytest.mark.db
async def test_review_reads_funnel_and_edits(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="main-review")
    ws = await make_workspace(db_session, org=org)
    c = _agent_campaign(ws.id, objective="x", owners={"sequence": "agent"})
    db_session.add(c)
    await db_session.flush()
    llm = FakeLLM(
        [
            tool_turn("read_funnel", {}, call_id="f1"),
            tool_turn(
                "set_sequence", {"steps": [{"channel": "linkedin", "delay_days": 0}]}, call_id="s1"
            ),
            text_turn("Adjusted."),
        ]
    )
    res = await review_campaign(db_session, llm=llm, campaign=c, organization_id=org.id)
    assert res.status == "done"
    assert len(c.sequence) == 1


@pytest.mark.db
async def test_deterministic_design(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="main-det")
    ws = await make_workspace(db_session, org=org)
    c = _agent_campaign(
        ws.id,
        objective="Hire backend engineers in NYC",
        owners={"audience": "agent", "sequence": "agent"},
    )
    db_session.add(c)
    await db_session.flush()
    await deterministic_design(db_session, campaign=c, organization_id=org.id)
    assert c.criteria.get("keywords")  # parse_brief fallback populated criteria
    assert len(c.sequence) == 3  # the default sequence


# --- endpoints ---------------------------------------------------------------


@pytest.mark.db
async def test_intake_and_design_endpoints(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    h = await _setup(db_client, "main-ep")
    intake = await db_client.post(
        "/agent/intake", json={"text": "Hire a senior backend engineer"}, headers=h
    )
    assert intake.status_code == 200
    assert intake.json()["objective"]

    c = _agent_campaign(
        h["X-Workspace-Id"],
        objective="Hire backend eng",
        owners={"audience": "agent", "sequence": "agent"},
    )
    db_session.add(c)
    await db_session.flush()
    design = await db_client.post("/agent/design", json={"campaign_id": c.id}, headers=h)
    assert design.status_code == 200
    body = design.json()
    assert body["status"] == "done"
    assert len(body["sequence"]) == 3  # deterministic fallback set the default sequence
