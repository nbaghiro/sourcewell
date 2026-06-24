"""Workspace analytics: funnel, channel reply-rates, per-campaign performance, activity feed."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import Select, distinct, func, select

from app.deps import ContextDep, SessionDep, require_workspace
from app.models import (
    Campaign,
    Channel,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
)

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


def _rate(num: int, den: int) -> float:
    return round(num / den, 3) if den else 0.0


@router.get("", response_model=AnalyticsOut)
async def analytics(ctx: ContextDep, session: SessionDep) -> AnalyticsOut:
    ws = require_workspace(ctx)

    async def scalar(stmt: Select[tuple[int]]) -> int:
        return int((await session.execute(stmt)).scalar_one())

    sourced = await scalar(
        select(func.count()).select_from(Enrollment).where(Enrollment.workspace_id == ws)
    )
    contacted = await scalar(
        select(func.count(distinct(Message.enrollment_id))).where(
            Message.workspace_id == ws,
            Message.direction == MessageDirection.outbound,
            Message.status == MessageStatus.sent,
        )
    )
    replied = await scalar(
        select(func.count(distinct(Message.enrollment_id))).where(
            Message.workspace_id == ws, Message.direction == MessageDirection.inbound
        )
    )
    handed_off = await scalar(
        select(func.count())
        .select_from(Enrollment)
        .where(Enrollment.workspace_id == ws, Enrollment.state == EnrollmentState.handed_off)
    )
    funnel = FunnelOut(
        sourced=sourced,
        contacted=contacted,
        replied=replied,
        handed_off=handed_off,
    )

    sent_by_ch: dict[Channel, int] = {
        ch: cnt
        for ch, cnt in (
            await session.execute(
                select(Message.channel, func.count())
                .where(
                    Message.workspace_id == ws,
                    Message.direction == MessageDirection.outbound,
                    Message.status == MessageStatus.sent,
                )
                .group_by(Message.channel)
            )
        )
        .tuples()
        .all()
    }
    repl_by_ch: dict[Channel, int] = {
        ch: cnt
        for ch, cnt in (
            await session.execute(
                select(Message.channel, func.count())
                .where(Message.workspace_id == ws, Message.direction == MessageDirection.inbound)
                .group_by(Message.channel)
            )
        )
        .tuples()
        .all()
    }
    channels = [
        ChannelStatOut(
            channel=ch.value,
            sent=int(sent_by_ch.get(ch, 0)),
            replied=int(repl_by_ch.get(ch, 0)),
            reply_rate=_rate(int(repl_by_ch.get(ch, 0)), int(sent_by_ch.get(ch, 0))),
        )
        for ch in (Channel.email, Channel.linkedin)
    ]

    sourced_by_c: dict[str, int] = {
        cid: cnt
        for cid, cnt in (
            await session.execute(
                select(Enrollment.campaign_id, func.count())
                .where(Enrollment.workspace_id == ws)
                .group_by(Enrollment.campaign_id)
            )
        )
        .tuples()
        .all()
    }
    replied_by_c: dict[str, int] = {
        cid: cnt
        for cid, cnt in (
            await session.execute(
                select(Enrollment.campaign_id, func.count(distinct(Enrollment.id)))
                .select_from(Message)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .where(Message.workspace_id == ws, Message.direction == MessageDirection.inbound)
                .group_by(Enrollment.campaign_id)
            )
        )
        .tuples()
        .all()
    }
    handed_by_c: dict[str, int] = {
        cid: cnt
        for cid, cnt in (
            await session.execute(
                select(Enrollment.campaign_id, func.count())
                .where(
                    Enrollment.workspace_id == ws, Enrollment.state == EnrollmentState.handed_off
                )
                .group_by(Enrollment.campaign_id)
            )
        )
        .tuples()
        .all()
    }
    campaign_rows = (
        (
            await session.execute(
                select(Campaign).where(Campaign.workspace_id == ws).order_by(Campaign.created_at)
            )
        )
        .scalars()
        .all()
    )
    campaigns = [
        CampaignStatOut(
            id=c.id,
            name=c.name,
            status=c.status.value,
            sourced=int(sourced_by_c.get(c.id, 0)),
            replied=int(replied_by_c.get(c.id, 0)),
            handed_off=int(handed_by_c.get(c.id, 0)),
            reply_rate=_rate(int(replied_by_c.get(c.id, 0)), int(sourced_by_c.get(c.id, 0))),
        )
        for c in campaign_rows
    ]

    msg_rows = (
        (
            await session.execute(
                select(Message, Contact, Campaign)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .join(Campaign, Enrollment.campaign_id == Campaign.id)
                .where(Message.workspace_id == ws, Message.status != MessageStatus.draft)
                .order_by(Message.created_at.desc())
                .limit(24)
            )
        )
        .tuples()
        .all()
    )
    activity = [
        ActivityOut(
            id=m.id,
            type="reply" if m.direction == MessageDirection.inbound else "sent",
            title=(
                f"{c.full_name} replied"
                if m.direction == MessageDirection.inbound
                else f"Sent to {c.full_name}"
            ),
            body=m.body[:80],
            campaign_name=camp.name,
            channel=m.channel.value,
            created_at=m.created_at.isoformat() if m.created_at else None,
        )
        for m, c, camp in msg_rows
    ]

    return AnalyticsOut(funnel=funnel, channels=channels, campaigns=campaigns, activity=activity)
