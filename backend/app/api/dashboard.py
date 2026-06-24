"""Dashboard HTTP layer: the read-only workspace summary endpoint."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.services.insights.dashboard import workspace_dashboard

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
    data = await workspace_dashboard(session, workspace_id=ws)
    return DashboardSummary(
        stats=DashboardStats(
            active_campaigns=data.stats.active_campaigns,
            contacts=data.stats.contacts,
            awaiting_approval=data.stats.awaiting_approval,
            replies_7d=data.stats.replies_7d,
        ),
        campaigns=[
            DashboardCampaign(
                id=c.id,
                name=c.name,
                status=c.status,
                autonomy_mode=c.autonomy_mode,
                sourced=c.sourced,
                awaiting=c.awaiting,
                replies=c.replies,
            )
            for c in data.campaigns
        ],
        approvals=[
            DashboardApproval(
                enrollment_id=a.enrollment_id,
                message_id=a.message_id,
                contact_name=a.contact_name,
                contact_avatar=a.contact_avatar,
                title=a.title,
                subject=a.subject,
                score=a.score,
            )
            for a in data.approvals
        ],
        recent_replies=[
            DashboardReply(
                contact_name=r.contact_name,
                snippet=r.snippet,
                state=r.state,
            )
            for r in data.recent_replies
        ],
    )
