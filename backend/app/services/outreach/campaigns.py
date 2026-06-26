"""Campaigns: CRUD service (create/list/get)."""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonList, JsonObject
from app.models import (
    Authorship,
    AutonomyLevel,
    AutonomyMode,
    Campaign,
    CampaignStatus,
)


async def create_campaign(
    session: AsyncSession,
    *,
    workspace_id: str,
    name: str,
    criteria: JsonObject,
    sequence: JsonList,
    autonomy_mode: AutonomyMode,
    from_email: str | None,
    objective: str | None = None,
    autonomy_level: AutonomyLevel = AutonomyLevel.assisted,
    authored_by: Authorship = Authorship.human,
    seed_contact_ids: list[str] | None = None,
) -> Campaign:
    agent_authored = authored_by == Authorship.agent
    campaign = Campaign(
        workspace_id=workspace_id,
        name=name,
        status=CampaignStatus.active,
        autonomy_mode=autonomy_mode,
        autonomy_level=autonomy_level,
        authored_by=authored_by,
        objective=objective,
        from_email=from_email,
        criteria=criteria,
        sequence=sequence,
        # Active campaigns source on the next worker tick; the agent owns the sections it authored.
        next_source_at=datetime.now(UTC),
        field_owners={"audience": "agent", "sequence": "agent"} if agent_authored else {},
        constraints={"seed_contact_ids": seed_contact_ids} if seed_contact_ids else {},
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def get_campaign(session: AsyncSession, *, workspace_id: str, campaign_id: str) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="campaign not found")
    return campaign


async def list_campaigns(session: AsyncSession, *, workspace_id: str) -> list[Campaign]:
    rows = await session.execute(
        select(Campaign).where(Campaign.workspace_id == workspace_id).order_by(Campaign.created_at)
    )
    return list(rows.scalars().all())
