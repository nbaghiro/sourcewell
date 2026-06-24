"""Notification feed (service layer): synthesize a lightweight feed from recent activity.

There's no separate notifications table; the feed is composed from recent inbound replies, recent
hand-offs, the count of drafts waiting on approval, and the user's last-seen marker (for unread).
HTTP endpoints + response schemas live in `app/api/notifications.py`.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    User,
)


@dataclass(frozen=True)
class FeedItem:
    id: str
    type: str
    title: str
    body: str
    contact_name: str
    contact_avatar: str | None
    enrollment_id: str
    created_at: str | None


@dataclass(frozen=True)
class Feed:
    items: list[FeedItem]
    approvals_waiting: int
    unread: int


async def build_feed(session: AsyncSession, *, workspace_id: str, user: User | None) -> Feed:
    """Compose the notification feed for a workspace (recent replies + hand-offs + approvals)."""
    items: list[FeedItem] = []

    # Recent inbound replies.
    reply_rows = (
        (
            await session.execute(
                select(Message, Contact)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .where(
                    Message.workspace_id == workspace_id,
                    Message.direction == MessageDirection.inbound,
                )
                .order_by(Message.created_at.desc())
                .limit(8)
            )
        )
        .tuples()
        .all()
    )
    for m, c in reply_rows:
        items.append(
            FeedItem(
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
                    Enrollment.workspace_id == workspace_id,
                    Enrollment.state == EnrollmentState.handed_off,
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
            FeedItem(
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
                .where(
                    Message.workspace_id == workspace_id,
                    Message.status == MessageStatus.draft,
                )
            )
        ).scalar_one()
    )
    seen = user.notifications_seen_at if user else None
    unread = sum(1 for i in items if seen is None or (i.created_at or "") > seen.isoformat())
    return Feed(items=items, approvals_waiting=approvals_waiting, unread=unread)


async def mark_all_read(session: AsyncSession, *, user: User | None) -> None:
    """Stamp the user's last-seen marker so the feed's unread count resets."""
    if user is not None:
        user.notifications_seen_at = datetime.now(UTC)
        await session.flush()
