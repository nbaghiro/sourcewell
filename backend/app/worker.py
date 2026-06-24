"""Autonomous send engine — poll for due enrollments and tick each once.

The background entrypoint (peer to `main.py`): run with `make worker` / `python -m app.worker`.
`run_due` is also called by the admin `/admin/run-due` endpoint (see `api/runtime.py`) to step the
engine by hand. Rate-limiting (`can_send_now`) is send-policy — see `services/outreach/governor.py`.
"""

import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import SessionLocal
from app.core.logging import configure_logging, logger
from app.models import Enrollment, EnrollmentState
from app.services.outreach.enrollment import tick

_POLL_SECONDS = 10
_ACTIONABLE = (
    EnrollmentState.active,
    EnrollmentState.scheduled,
    EnrollmentState.awaiting_reply,
)


async def run_due(session: AsyncSession, *, now: datetime, limit: int = 200) -> dict[str, int]:
    """Find enrollments whose `next_run_at` is due and tick each once (`FOR UPDATE SKIP LOCKED`)."""
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


async def _loop() -> None:
    while True:
        async with SessionLocal() as session:
            result = await run_due(session, now=datetime.now(UTC))
            await session.commit()
        if result["processed"]:
            logger.info("worker ticked %s enrollment(s)", result["processed"])
        await asyncio.sleep(_POLL_SECONDS)


def main() -> None:
    configure_logging()
    logger.info("sourcewell runtime worker started")
    asyncio.run(_loop())


if __name__ == "__main__":
    main()
