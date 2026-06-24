"""Notification feed HTTP endpoints.

The feed is synthesized in `app.services.workspace.notifications`; this module is the HTTP layer
that maps the typed service result onto the response schemas.
"""

from fastapi import APIRouter
from pydantic import BaseModel

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.models import User
from app.services.workspace import notifications as notifications_service

router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationItem(BaseModel):
    id: str
    type: str
    title: str
    body: str
    contact_name: str
    contact_avatar: str | None
    enrollment_id: str
    created_at: str | None


class NotificationsOut(BaseModel):
    items: list[NotificationItem]
    approvals_waiting: int
    unread: int


class StatusOut(BaseModel):
    status: str


@router.get("", response_model=NotificationsOut)
async def notifications(ctx: ContextDep, session: SessionDep) -> NotificationsOut:
    ws = require_workspace(ctx)
    user = await session.get(User, ctx.user_id)
    feed = await notifications_service.build_feed(session, workspace_id=ws, user=user)
    return NotificationsOut(
        items=[
            NotificationItem(
                id=i.id,
                type=i.type,
                title=i.title,
                body=i.body,
                contact_name=i.contact_name,
                contact_avatar=i.contact_avatar,
                enrollment_id=i.enrollment_id,
                created_at=i.created_at,
            )
            for i in feed.items
        ],
        approvals_waiting=feed.approvals_waiting,
        unread=feed.unread,
    )


@router.post("/read", response_model=StatusOut)
async def mark_all_read(ctx: ContextDep, session: SessionDep) -> StatusOut:
    user = await session.get(User, ctx.user_id)
    await notifications_service.mark_all_read(session, user=user)
    return StatusOut(status="read")
