"""Read-only dashboard aggregation for the current workspace."""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import Select, func, select

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
from app.workspace.tenancy import ContextDep, SessionDep, require_workspace

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardStats(BaseModel):
    active_campaigns: int
    contacts: int
    awaiting_approval: int
    replies_7d: int


class DashboardCampaign(BaseModel):
    id: str
    name: str
    status: str
    autonomy_mode: str
    sourced: int
    awaiting: int
    replies: int


class DashboardApproval(BaseModel):
    enrollment_id: str
    message_id: str
    contact_name: str
    contact_avatar: str | None
    title: str | None
    subject: str | None
    score: int


class DashboardReply(BaseModel):
    contact_name: str
    snippet: str
    state: str


class DashboardSummary(BaseModel):
    stats: DashboardStats
    campaigns: list[DashboardCampaign]
    approvals: list[DashboardApproval]
    recent_replies: list[DashboardReply]


@router.get("/summary", response_model=DashboardSummary)
async def summary(ctx: ContextDep, session: SessionDep) -> DashboardSummary:
    ws = require_workspace(ctx)
    since = datetime.now(UTC) - timedelta(days=7)

    async def scalar(stmt: Select[tuple[int]]) -> int:
        return int((await session.execute(stmt)).scalar_one())

    stats = DashboardStats(
        active_campaigns=await scalar(
            select(func.count())
            .select_from(Campaign)
            .where(Campaign.workspace_id == ws, Campaign.status == CampaignStatus.active)
        ),
        contacts=await scalar(
            select(func.count()).select_from(Contact).where(Contact.workspace_id == ws)
        ),
        awaiting_approval=await scalar(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.workspace_id == ws,
                Enrollment.state == EnrollmentState.awaiting_approval,
            )
        ),
        replies_7d=await scalar(
            select(func.count())
            .select_from(Message)
            .where(
                Message.workspace_id == ws,
                Message.direction == MessageDirection.inbound,
                Message.created_at >= since,
            )
        ),
    )

    # Per-campaign rollups.
    sourced_by: dict[str, int] = {
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
    awaiting_by: dict[str, int] = {
        cid: cnt
        for cid, cnt in (
            await session.execute(
                select(Enrollment.campaign_id, func.count())
                .where(
                    Enrollment.workspace_id == ws,
                    Enrollment.state == EnrollmentState.awaiting_approval,
                )
                .group_by(Enrollment.campaign_id)
            )
        )
        .tuples()
        .all()
    }
    replies_by: dict[str, int] = {
        cid: cnt
        for cid, cnt in (
            await session.execute(
                select(Enrollment.campaign_id, func.count())
                .select_from(Message)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .where(Message.workspace_id == ws, Message.direction == MessageDirection.inbound)
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
        DashboardCampaign(
            id=c.id,
            name=c.name,
            status=c.status.value,
            autonomy_mode=c.autonomy_mode.value,
            sourced=int(sourced_by.get(c.id, 0)),
            awaiting=int(awaiting_by.get(c.id, 0)),
            replies=int(replies_by.get(c.id, 0)),
        )
        for c in campaign_rows
    ]

    # Approval queue (top drafts by fit).
    approvals = [
        DashboardApproval(
            enrollment_id=e.id,
            message_id=m.id,
            contact_name=ct.full_name,
            contact_avatar=ct.avatar_url,
            title=ct.title,
            subject=m.subject,
            score=e.score,
        )
        for m, e, ct in (
            await session.execute(
                select(Message, Enrollment, Contact)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .where(Message.workspace_id == ws, Message.status == MessageStatus.draft)
                .order_by(Enrollment.score.desc())
                .limit(6)
            )
        )
        .tuples()
        .all()
    ]

    # Recent inbound replies.
    recent_replies = [
        DashboardReply(
            contact_name=ct.full_name,
            snippet=(m.body[:80] + "…") if len(m.body) > 80 else m.body,
            state=e.state.value,
        )
        for m, e, ct in (
            await session.execute(
                select(Message, Enrollment, Contact)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .where(Message.workspace_id == ws, Message.direction == MessageDirection.inbound)
                .order_by(Message.created_at.desc())
                .limit(6)
            )
        )
        .tuples()
        .all()
    ]

    return DashboardSummary(
        stats=stats,
        campaigns=campaigns,
        approvals=approvals,
        recent_replies=recent_replies,
    )
