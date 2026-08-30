"""The demo builder seeds a rich three-vertical demo org used as a test fixture."""

from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Contact, Organization, Workspace
from tests.seed.builder import seed_demo


@pytest.mark.db
async def test_seed_demo_builds_a_realistic_spread(db_session: AsyncSession) -> None:
    # Pin to the 1st of a month so the historical narrative activity stays out of the current
    # usage period and only the seeded in-period batch counts toward credits.
    now = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
    summary = await seed_demo(db_session, reset=False, now=now)

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

    # Current-period activity drives the pooled credit meter; usage is derived correctly:
    #   used = emails*1 + linkedin_dms*1 + inmails*2 + sourced*1
    # The seeded campaigns don't opt into InMail, so their LinkedIn sends are ordinary DMs and bill
    # at the DM rate — they used to be counted as InMails and charged double.
    credits = summary["credits"]
    assert credits["emails"] == 600
    assert credits["linkedin_dms"] == 100
    assert credits["inmails"] == 0
    assert credits["sourced"] == 200
    assert credits["used"] == (
        credits["emails"] + credits["linkedin_dms"] + credits["inmails"] * 2 + credits["sourced"]
    )
    assert credits["used"] == 900
    # Demo starts on the free plan (200) → the seeded usage reads as over-limit until an upgrade.
    assert credits["allowance"] == 200
    assert credits["pct"] == 450
