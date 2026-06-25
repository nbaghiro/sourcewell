"""The source_due scheduler + the cockpit read-models (runs feed + funnel)."""

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app import worker
from app.models import (
    Campaign,
    CampaignStatus,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
)
from app.services.cockpit.runs import campaign_funnel, recent_runs
from tests.factories import make_org, make_workspace
from tests.fake_llm import FakeLLM, text_turn, tool_turn


def _due_campaign(ws_id: str, *, due: datetime) -> Campaign:
    return Campaign(
        workspace_id=ws_id,
        name="C",
        status=CampaignStatus.active,
        criteria={"titles": ["VP of Sales"]},
        sequence=[],
        next_source_at=due,
    )


# --- the scheduler -----------------------------------------------------------


@pytest.mark.db
async def test_source_due_deterministic(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "default_llm", lambda: None)  # force the LLM-free fallback
    org = await make_org(db_session, slug="sd-det")
    ws = await make_workspace(db_session, org=org)
    now = datetime.now(UTC)
    c = _due_campaign(ws.id, due=now - timedelta(minutes=1))
    db_session.add(c)
    await db_session.flush()

    res = await worker.run_source_due(db_session, now=now)
    assert res["sourced"] == 1
    assert c.next_source_at is not None and c.next_source_at > now  # re-armed for the next pass
    enr = (
        (await db_session.execute(select(Enrollment).where(Enrollment.campaign_id == c.id)))
        .scalars()
        .all()
    )
    assert enr and all(e.state == EnrollmentState.proposed for e in enr)


@pytest.mark.db
async def test_source_due_agent_path(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="sd-agent")
    ws = await make_workspace(db_session, org=org)
    now = datetime.now(UTC)
    c = _due_campaign(ws.id, due=now - timedelta(minutes=1))
    db_session.add(c)
    await db_session.flush()

    llm = FakeLLM(
        [
            tool_turn("search", {"limit": 5}, call_id="s1"),
            tool_turn("import", {"ids": ["h0", "h1", "h2"]}, call_id="i1"),
            text_turn("done"),
        ]
    )
    res = await worker.run_source_due(db_session, now=now, llm=llm)
    assert res["sourced"] == 1
    enr = (
        (await db_session.execute(select(Enrollment).where(Enrollment.campaign_id == c.id)))
        .scalars()
        .all()
    )
    assert len(enr) >= 1


@pytest.mark.db
async def test_source_due_skips_not_due(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(worker, "default_llm", lambda: None)
    org = await make_org(db_session, slug="sd-skip")
    ws = await make_workspace(db_session, org=org)
    now = datetime.now(UTC)
    c = _due_campaign(ws.id, due=now + timedelta(hours=1))  # future → not due
    db_session.add(c)
    await db_session.flush()
    res = await worker.run_source_due(db_session, now=now)
    assert res["sourced"] == 0


# --- the read-models ---------------------------------------------------------


@pytest.mark.db
async def test_recent_runs_returns_trace(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="sd-runs")
    ws = await make_workspace(db_session, org=org)
    now = datetime.now(UTC)
    c = _due_campaign(ws.id, due=now - timedelta(minutes=1))
    db_session.add(c)
    await db_session.flush()
    llm = FakeLLM([tool_turn("search", {"limit": 4}, call_id="s1"), text_turn("found some")])
    await worker.run_source_due(db_session, now=now, llm=llm)

    runs = await recent_runs(db_session, campaign_id=c.id)
    assert runs and runs[0].role == "sourcing"
    assert any(s.kind == "tool_call" for s in runs[0].steps)


@pytest.mark.db
async def test_campaign_funnel_counts(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="sd-funnel")
    ws = await make_workspace(db_session, org=org)
    c = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    contact = Contact(workspace_id=ws.id, full_name="Lee", skills=[], tags=[])
    db_session.add_all([c, contact])
    await db_session.flush()
    enr = Enrollment(
        workspace_id=ws.id,
        campaign_id=c.id,
        contact_id=contact.id,
        state=EnrollmentState.awaiting_reply,
    )
    db_session.add(enr)
    await db_session.flush()
    db_session.add_all(
        [
            Message(
                workspace_id=ws.id,
                enrollment_id=enr.id,
                direction=MessageDirection.outbound,
                status=MessageStatus.sent,
                body="hi",
            ),
            Message(
                workspace_id=ws.id,
                enrollment_id=enr.id,
                direction=MessageDirection.inbound,
                status=MessageStatus.received,
                body="re",
            ),
        ]
    )
    await db_session.flush()

    f = await campaign_funnel(db_session, campaign_id=c.id)
    assert (f.sourced, f.contacted, f.replied, f.handed_off) == (1, 1, 1, 0)


# --- the endpoints (auth + tenancy guard) ------------------------------------


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


@pytest.mark.db
async def test_agent_runs_and_funnel_endpoints(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    h = await _setup(db_client, "sd-ep")
    c = Campaign(workspace_id=h["X-Workspace-Id"], name="C", criteria={}, sequence=[])
    db_session.add(c)
    await db_session.flush()

    funnel = await db_client.get(f"/agent/funnel?campaign_id={c.id}", headers=h)
    assert funnel.status_code == 200
    assert funnel.json()["sourced"] == 0

    runs = await db_client.get(f"/agent/runs?campaign_id={c.id}", headers=h)
    assert runs.status_code == 200
    assert runs.json() == []

    # tenancy guard: a campaign not in the caller's workspace → 404
    missing = await db_client.get("/agent/funnel?campaign_id=does-not-exist", headers=h)
    assert missing.status_code == 404
