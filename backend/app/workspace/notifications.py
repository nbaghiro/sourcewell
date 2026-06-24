"""A lightweight notification feed synthesized from recent activity (no separate table)."""

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import func, select

from app.models import (
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    User,
)
from app.workspace.tenancy import ContextDep, SessionDep, require_workspace

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
    items: list[NotificationItem] = []

    # Recent inbound replies.
    reply_rows = (
        (
            await session.execute(
                select(Message, Contact)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .where(Message.workspace_id == ws, Message.direction == MessageDirection.inbound)
                .order_by(Message.created_at.desc())
                .limit(8)
            )
        )
        .tuples()
        .all()
    )
    for m, c in reply_rows:
        items.append(
            NotificationItem(
                id=m.id,
                type="reply",
                title=f"{c.full_name} replied",
                body=m.body[:80],
                contact_name=c.full_name,
                contact_avatar=c.avatar_url,
                enrollment_id=m.enrollment_id,
                created_at=m.created_at.isoformat() if m.created_at else None,
            )
        )

    # Recent hand-offs.
    handoff_rows = (
        (
            await session.execute(
                select(Enrollment, Contact)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .where(
                    Enrollment.workspace_id == ws, Enrollment.state == EnrollmentState.handed_off
                )
                .order_by(Enrollment.updated_at.desc())
                .limit(4)
            )
        )
        .tuples()
        .all()
    )
    for e, c in handoff_rows:
        items.append(
            NotificationItem(
                id=e.id,
                type="handoff",
                title=f"{c.full_name} handed off",
                body="Interested — ready for your team",
                contact_name=c.full_name,
                contact_avatar=c.avatar_url,
                enrollment_id=e.id,
                created_at=e.updated_at.isoformat() if e.updated_at else None,
            )
        )

    items.sort(key=lambda i: i.created_at or "", reverse=True)
    items = items[:10]

    approvals_waiting = int(
        (
            await session.execute(
                select(func.count())
                .select_from(Message)
                .where(Message.workspace_id == ws, Message.status == MessageStatus.draft)
            )
        ).scalar_one()
    )
    user = await session.get(User, ctx.user_id)
    seen = user.notifications_seen_at if user else None
    unread = sum(1 for i in items if seen is None or (i.created_at or "") > seen.isoformat())
    return NotificationsOut(items=items, approvals_waiting=approvals_waiting, unread=unread)


@router.post("/read", response_model=StatusOut)
async def mark_all_read(ctx: ContextDep, session: SessionDep) -> StatusOut:
    user = await session.get(User, ctx.user_id)
    if user is not None:
        user.notifications_seen_at = datetime.now(UTC)
        await session.flush()
    return StatusOut(status="read")
