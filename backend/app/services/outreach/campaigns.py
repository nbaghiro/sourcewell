"""Campaigns: CRUD service (create/list/get)."""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonList, JsonObject
from app.models import (
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
) -> Campaign:
    campaign = Campaign(
        workspace_id=workspace_id,
        name=name,
        status=CampaignStatus.active,
        autonomy_mode=autonomy_mode,
        from_email=from_email,
        criteria=criteria,
        sequence=sequence,
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
