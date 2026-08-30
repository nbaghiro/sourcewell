"""The policy chain: platform → partner → organization → workspace → campaign, nearest wins."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core import policy
from app.models import Campaign, Organization, Partner, Workspace, WorkspaceKind
from app.services.outreach.campaigns import default_autonomy


async def _chain(
    session: AsyncSession, slug: str
) -> tuple[Partner, Organization, Workspace, Campaign]:
    partner = Partner(name="Reseller", slug=f"{slug}-partner")
    session.add(partner)
    await session.flush()
    org = Organization(name="Org", slug=slug, partner_id=partner.id)
    session.add(org)
    await session.flush()
    ws = Workspace(organization_id=org.id, name="W", kind=WorkspaceKind.team)
    session.add(ws)
    await session.flush()
    campaign = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    session.add(campaign)
    await session.flush()
    return partner, org, ws, campaign


@pytest.mark.db
async def test_each_level_overrides_the_one_above_it(db_session: AsyncSession) -> None:
    partner, org, ws, campaign = await _chain(db_session, "chain-levels")

    async def cap() -> int:
        return (await policy.for_campaign(db_session, campaign=campaign)).get_int("daily_cap_email")

    assert await cap() == 120  # platform default

    partner.settings = {"daily_cap_email": 90}
    await db_session.flush()
    assert await cap() == 90

    org.settings = {"daily_cap_email": 60}
    await db_session.flush()
    assert await cap() == 60

    ws.settings = {"daily_cap_email": 30}
    await db_session.flush()
    assert await cap() == 30

    campaign.constraints = {"daily_cap_email": 10}
    await db_session.flush()
    assert await cap() == 10


@pytest.mark.db
async def test_workspace_chain_stops_below_the_campaign(db_session: AsyncSession) -> None:
    _partner, _org, ws, campaign = await _chain(db_session, "chain-ws")
    ws.settings = {"brand_voice": "warm and direct"}
    campaign.constraints = {"brand_voice": "blunt"}
    await db_session.flush()

    resolved = await policy.for_workspace(db_session, workspace_id=ws.id)
    assert resolved.get_str("brand_voice") == "warm and direct"
    campaign_view = await policy.for_campaign(db_session, campaign=campaign)
    assert campaign_view.get_str("brand_voice") == "blunt"


@pytest.mark.db
async def test_effective_flattens_the_whole_chain(db_session: AsyncSession) -> None:
    partner, org, ws, _campaign = await _chain(db_session, "chain-flat")
    partner.settings = {"vertical": "sales", "warmup_enabled": True}
    org.settings = {"daily_cap_linkedin": 40}
    ws.settings = {"vertical": "recruiting"}
    await db_session.flush()

    effective = (await policy.for_workspace(db_session, workspace_id=ws.id)).effective()
    assert effective["vertical"] == "recruiting"  # nearest wins
    assert effective["warmup_enabled"] is True  # inherited from the partner
    assert effective["daily_cap_linkedin"] == 40  # inherited from the org
    assert effective["send_window_start"] == 8  # untouched platform default


def test_typed_accessors_coerce_and_fall_back() -> None:
    resolved = policy.Policy(
        layers=(
            {"daily_cap_email": "45", "warmup_enabled": "yes", "providers": ["pdl", 7]},
            policy.PLATFORM_DEFAULTS,
        )
    )
    assert resolved.get_int("daily_cap_email") == 45
    assert resolved.get_bool("warmup_enabled") is True
    assert resolved.get_str_list("providers") == ["pdl"]
    # An unusable value falls back to the platform default rather than blowing up.
    junk = policy.Policy(layers=({"daily_cap_email": {"nope": 1}}, policy.PLATFORM_DEFAULTS))
    assert junk.get_int("daily_cap_email") == 120


@pytest.mark.db
async def test_autonomy_default_is_applied_to_new_campaigns(db_session: AsyncSession) -> None:
    _partner, org, ws, _campaign = await _chain(db_session, "chain-autonomy")
    assert (await default_autonomy(db_session, workspace_id=ws.id)).value == "assisted"

    org.settings = {"autonomy_default": "full"}
    await db_session.flush()
    assert (await default_autonomy(db_session, workspace_id=ws.id)).value == "full"

    # An unknown value never leaves a campaign in an invalid state.
    ws.settings = {"autonomy_default": "yolo"}
    await db_session.flush()
    assert (await default_autonomy(db_session, workspace_id=ws.id)).value == "assisted"
