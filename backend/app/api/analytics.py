"""Analytics HTTP layer: the workspace funnel / channel / campaign / activity endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.guards import require_workspace
from app.deps import ContextDep, SessionDep
from app.services.insights.analytics import workspace_analytics

router = APIRouter(prefix="/analytics", tags=["analytics"])


class FunnelOut(BaseModel):
    sourced: int
    contacted: int
    replied: int
    handed_off: int


class ChannelStatOut(BaseModel):
    channel: str
    sent: int
    replied: int
    reply_rate: float


class CampaignStatOut(BaseModel):
    id: str
    name: str
    status: str
    sourced: int
    replied: int
    handed_off: int
    reply_rate: float


class ActivityOut(BaseModel):
    id: str
    type: str
    title: str
    body: str
    campaign_name: str
    channel: str
    created_at: str | None


class AnalyticsOut(BaseModel):
    funnel: FunnelOut
    channels: list[ChannelStatOut]
    campaigns: list[CampaignStatOut]
    activity: list[ActivityOut]


@router.get("", response_model=AnalyticsOut)
async def analytics(ctx: ContextDep, session: SessionDep) -> AnalyticsOut:
    ws = require_workspace(ctx)
    data = await workspace_analytics(session, workspace_id=ws)
    return AnalyticsOut(
        funnel=FunnelOut(
            sourced=data.funnel.sourced,
            contacted=data.funnel.contacted,
            replied=data.funnel.replied,
            handed_off=data.funnel.handed_off,
        ),
        channels=[
            ChannelStatOut(
                channel=ch.channel,
                sent=ch.sent,
                replied=ch.replied,
                reply_rate=ch.reply_rate,
            )
            for ch in data.channels
        ],
        campaigns=[
            CampaignStatOut(
                id=c.id,
                name=c.name,
                status=c.status,
                sourced=c.sourced,
                replied=c.replied,
                handed_off=c.handed_off,
                reply_rate=c.reply_rate,
            )
            for c in data.campaigns
        ],
        activity=[
            ActivityOut(
                id=a.id,
                type=a.type,
                title=a.title,
                body=a.body,
                campaign_name=a.campaign_name,
                channel=a.channel,
                created_at=a.created_at,
            )
            for a in data.activity
        ],
    )
