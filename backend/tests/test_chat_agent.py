"""The Main-agent chat — text + typed entities + the interactive apply-audience loop."""

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.chat import run_chat
from app.api import agent as agent_api
from app.models import Authorship, Campaign, Contact, Enrollment, EnrollmentState
from tests.factories import make_org, make_workspace
from tests.fake_llm import FakeLLM, text_turn, tool_turn


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


# --- run_chat → entities -----------------------------------------------------


@pytest.mark.db
async def test_run_chat_emits_funnel_and_candidates(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="chat-show")
    ws = await make_workspace(db_session, org=org)
    c = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(workspace_id=ws.id, full_name="Lee", skills=[], tags=[])
    db_session.add_all([c, contact])
    await db_session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=c.id,
        contact_id=contact.id,
        state=EnrollmentState.proposed,
        score=70,
    )
    db_session.add(enr)
    await db_session.flush()

    llm = FakeLLM(
        [
            tool_turn("show_funnel", {}, call_id="f1"),
            tool_turn("show_candidates", {}, call_id="c1"),
            text_turn("Here's the campaign status."),
        ]
    )
    res = await run_chat(
        db_session,
        llm=llm,
        workspace_id=ws.id,
        organization_id=org.id,
        message="how's it going?",
        campaign_id=c.id,
    )
    types = [e.get("type") for e in res.entities]
    assert "funnel" in types
    assert "candidate_list" in types
    cl = next(e for e in res.entities if e.get("type") == "candidate_list")
    data = cl.get("data")
    assert isinstance(data, dict) and data.get("total") == 1


@pytest.mark.db
async def test_run_chat_preview_audience_carries_action(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="chat-preview")
    ws = await make_workspace(db_session, org=org)
    c = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    db_session.add(c)
    await db_session.flush()
    llm = FakeLLM(
        [
            tool_turn("preview_audience", {"titles": ["VP of Sales"]}, call_id="p1"),
            text_turn("Preview."),
        ]
    )
    res = await run_chat(
        db_session,
        llm=llm,
        workspace_id=ws.id,
        organization_id=org.id,
        message="find VPs",
        campaign_id=c.id,
    )
    prev = next(e for e in res.entities if e.get("type") == "audience_preview")
    action = prev.get("action")
    assert isinstance(action, dict)
    assert action.get("verb") == "apply"
    assert action.get("endpoint") == "/agent/apply-audience"


# --- the interactive loop: apply-audience endpoint ---------------------------


@pytest.mark.db
async def test_apply_audience_endpoint_pins_section(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    h = await _setup(db_client, "chat-apply")
    c = Campaign(
        workspace_id=h["X-Workspace-Id"],
        name="C",
        authored_by=Authorship.agent,
        field_owners={"audience": "agent"},
        criteria={},
        sequence=[],
    )
    db_session.add(c)
    await db_session.flush()
    resp = await db_client.post(
        "/agent/apply-audience",
        json={"campaign_id": c.id, "criteria": {"titles": ["VP of Sales"]}},
        headers=h,
    )
    assert resp.status_code == 200
    assert resp.json()["status"] == "applied"
    assert c.criteria == {"titles": ["VP of Sales"]}
    assert c.field_owners.get("audience") == "human"  # a human apply pins the section


# --- the chat endpoint falls back to the legacy copilot when no LLM ----------


@pytest.mark.db
async def test_chat_endpoint_fallback_no_llm(
    db_client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(agent_api, "default_llm", lambda: None)
    h = await _setup(db_client, "chat-fb")
    resp = await db_client.post("/agent/chat", json={"message": "how's it going?"}, headers=h)
    assert resp.status_code == 200
    body = resp.json()
    assert "reply" in body
    assert body["entities"] == []  # legacy path emits no entities
