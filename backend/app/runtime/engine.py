"""The polling driver: find enrollments whose `next_run_at` is due and tick each once.

In production this runs `FOR UPDATE SKIP LOCKED` across worker processes; for the alpha the
single worker (and the admin run-due endpoint) call `run_due` against one session.
"""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Enrollment, EnrollmentState
from app.outreach.enrollment import tick

_ACTIONABLE = (
    EnrollmentState.active,
    EnrollmentState.scheduled,
    EnrollmentState.awaiting_reply,
)


async def run_due(session: AsyncSession, *, now: datetime, limit: int = 200) -> dict[str, int]:
    stmt = (
        select(Enrollment)
        .where(
            Enrollment.next_run_at.is_not(None),
            Enrollment.next_run_at <= now,
            Enrollment.state.in_(_ACTIONABLE),
        )
        .order_by(Enrollment.next_run_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    due = list((await session.execute(stmt)).scalars().all())
    for enrollment in due:
        await tick(session, enrollment=enrollment, now=now)
    return {"processed": len(due)}
