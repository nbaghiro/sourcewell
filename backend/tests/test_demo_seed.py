"""The demo builder seeds a rich three-vertical demo org used as a test fixture."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.demo.builder import seed_demo
from app.models import Contact, Organization, Workspace


@pytest.mark.db
async def test_seed_demo_builds_a_realistic_spread(db_session: AsyncSession) -> None:
    summary = await seed_demo(db_session, reset=False)

    assert summary["workspaces"] == 3
    states = summary["enrollments_by_state"]
    # A full pipeline spread across all states.
    assert states.get("proposed", 0) > 0
    assert states.get("awaiting_reply", 0) > 0
    assert states.get("awaiting_approval", 0) > 0
    assert states.get("scheduled", 0) > 0
    assert states.get("handed_off", 0) > 0
    assert states.get("opted_out", 0) > 0

    org = (
        await db_session.execute(select(Organization).where(Organization.slug == "acme-talent"))
    ).scalar_one()
    assert org.name == "Acme Talent"

    # Three named verticals.
    names = {
        w.name
        for w in (
            await db_session.execute(select(Workspace).where(Workspace.organization_id == org.id))
        )
        .scalars()
        .all()
    }
    assert names == {"Recruiting", "Enterprise Sales", "Partnerships"}

    # CRM enrichment is populated (notes/tags/firmographics).
    enriched = (
        await db_session.execute(
            select(Contact).where(Contact.industry.isnot(None), Contact.notes.isnot(None)).limit(1)
        )
    ).scalar_one_or_none()
    assert enriched is not None and enriched.tags
