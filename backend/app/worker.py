"""Autonomous send engine — poll for due enrollments and tick each once.

The background entrypoint (peer to `main.py`): run with `make worker` / `python -m app.worker`.
`run_due` is also called by the admin `/admin/run-due` endpoint (see `api/runtime.py`) to step the
engine by hand. Rate-limiting (`can_send_now`) is send-policy — see `services/outreach/governor.py`.
"""

import asyncio
from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sourcing import deterministic_source, run_sourcing
from app.core.db import SessionLocal
from app.core.logging import configure_logging, logger
from app.core.runtime import CAMPAIGN_DAILY_TOKEN_BUDGET, AgentLLM, default_llm
from app.models import (
    AgentRun,
    AutonomyLevel,
    Campaign,
    CampaignStatus,
    Enrollment,
    EnrollmentState,
    Workspace,
)
from app.services.outreach.enrollment import tick

_POLL_SECONDS = 10
_MAX_BACKOFF_SECONDS = 300
_SOURCE_LIMIT = 20
_SOURCE_INTERVAL_HOURS = 6
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
    processed = 0
    for enrollment in due:
        try:
            # Isolate each tick: a failure (e.g. a send error) can't roll back the others.
            async with session.begin_nested():
                await tick(session, enrollment=enrollment, now=now)
            processed += 1
        except Exception:
            logger.exception("worker: tick failed for enrollment %s", enrollment.id)
    return {"processed": processed}


async def _tokens_today(session: AsyncSession, *, campaign_id: str, now: datetime) -> int:
    """Agent tokens spent on a campaign since midnight UTC (the per-campaign daily cap)."""
    start = datetime(now.year, now.month, now.day, tzinfo=UTC)
    total = (
        await session.execute(
            select(func.coalesce(func.sum(AgentRun.tokens), 0)).where(
                AgentRun.campaign_id == campaign_id, AgentRun.created_at >= start
            )
        )
    ).scalar_one()
    return int(total)


async def _auto_approve(session: AsyncSession, *, campaign: Campaign, now: datetime) -> int:
    """Full-autonomy candidate gate: flip proposed enrollments to active (ready to send)."""
    if campaign.autonomy_level != AutonomyLevel.full:
        return 0
    proposed = list(
        (
            await session.execute(
                select(Enrollment).where(
                    Enrollment.campaign_id == campaign.id,
                    Enrollment.state == EnrollmentState.proposed,
                )
            )
        )
        .scalars()
        .all()
    )
    for enrollment in proposed:
        enrollment.state = EnrollmentState.active
        enrollment.next_run_at = now
    return len(proposed)


async def run_source_due(
    session: AsyncSession,
    *,
    now: datetime,
    llm: AgentLLM | None = None,
    limit: int = _SOURCE_LIMIT,
) -> dict[str, int]:
    """Find active campaigns whose `next_source_at` is due and run one sourcing pass each.

    Uses the Sourcing agent when an LLM is available and the campaign is under its daily token
    budget, else the deterministic fallback. Full-autonomy campaigns auto-approve their proposed
    candidates. Self-clocking via `next_source_at`; `FOR UPDATE SKIP LOCKED` for safe multi-worker.
    """
    client = llm if llm is not None else default_llm()
    stmt = (
        select(Campaign)
        .where(
            Campaign.status == CampaignStatus.active,
            Campaign.next_source_at.is_not(None),
            Campaign.next_source_at <= now,
        )
        .order_by(Campaign.next_source_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    due = list((await session.execute(stmt)).scalars().all())
    sourced = 0
    for campaign in due:
        try:
            async with session.begin_nested():  # isolate each campaign's sourcing pass
                ws = await session.get(Workspace, campaign.workspace_id)
                if ws is None:
                    continue
                over_budget = (
                    client is not None
                    and await _tokens_today(session, campaign_id=campaign.id, now=now)
                    >= CAMPAIGN_DAILY_TOKEN_BUDGET
                )
                if client is not None and not over_budget:
                    await run_sourcing(
                        session, llm=client, campaign=campaign, organization_id=ws.organization_id
                    )
                else:
                    await deterministic_source(
                        session, campaign=campaign, organization_id=ws.organization_id
                    )
                await _auto_approve(session, campaign=campaign, now=now)
                campaign.next_source_at = now + timedelta(hours=_SOURCE_INTERVAL_HOURS)
            sourced += 1
        except Exception:
            logger.exception("worker: sourcing failed for campaign %s", campaign.id)
    return {"sourced": sourced}


async def _loop() -> None:
    errors = 0
    while True:
        try:
            async with SessionLocal() as session:
                now = datetime.now(UTC)
                sent = await run_due(session, now=now)
                sourced = await run_source_due(session, now=now)
                await session.commit()
            errors = 0
            if sent["processed"] or sourced["sourced"]:
                logger.info(
                    "worker: ticked %s enrollment(s), sourced %s campaign(s)",
                    sent["processed"],
                    sourced["sourced"],
                )
            await asyncio.sleep(_POLL_SECONDS)
        except Exception:
            # Never let a transient failure kill the worker — log and back off, then retry.
            errors += 1
            delay = min(_POLL_SECONDS * 2**errors, _MAX_BACKOFF_SECONDS)
            logger.exception("worker: loop iteration failed; backing off %ss", delay)
            await asyncio.sleep(delay)


def main() -> None:
    configure_logging()
    logger.info("sourcewell runtime worker started")
    asyncio.run(_loop())


if __name__ == "__main__":
    main()
