"""create_campaign wires the agent-native fields: sourcing trigger + provenance + seeds."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Authorship, AutonomyMode
from app.services.outreach.campaigns import create_campaign
from tests.factories import make_org, make_workspace


@pytest.mark.db
async def test_create_campaign_schedules_sourcing_and_sets_provenance(
    db_session: AsyncSession,
) -> None:
    org = await make_org(db_session, slug="cc")
    ws = await make_workspace(db_session, org=org)
    c = await create_campaign(
        db_session,
        workspace_id=ws.id,
        name="Senior Backend Engineer",
        criteria={"titles": ["Senior Backend Engineer"]},
        sequence=[],
        autonomy_mode=AutonomyMode.approve_each,
        from_email=None,
        objective="Hire a senior backend engineer",
        authored_by=Authorship.agent,
        seed_contact_ids=["x1", "x2"],
    )
    # the worker can now see it: next_source_at set → the Sourcing agent runs on the next tick.
    assert c.next_source_at is not None
    assert c.authored_by == Authorship.agent
    assert c.field_owners == {"audience": "agent", "sequence": "agent"}
    assert c.constraints == {"seed_contact_ids": ["x1", "x2"]}
    assert c.objective == "Hire a senior backend engineer"


@pytest.mark.db
async def test_create_campaign_human_authored_leaves_sections_unowned(
    db_session: AsyncSession,
) -> None:
    org = await make_org(db_session, slug="cc2")
    ws = await make_workspace(db_session, org=org)
    c = await create_campaign(
        db_session,
        workspace_id=ws.id,
        name="Manual",
        criteria={},
        sequence=[],
        autonomy_mode=AutonomyMode.approve_each,
        from_email=None,
    )
    assert c.next_source_at is not None  # active campaigns still source
    assert c.authored_by == Authorship.human
    assert c.field_owners == {}
