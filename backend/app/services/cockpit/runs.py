"""Cockpit read-models: the agent-run trace feed + a per-campaign funnel.

Reads the new `AgentRun`/`AgentStep` traces and the enrollment/message tables — no new state.
"""

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonObject
from app.models import (
    AgentRun,
    AgentStep,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
)


@dataclass
class StepData:
    seq: int
    kind: str
    tool_name: str | None
    content: JsonObject


@dataclass
class RunData:
    id: str
    role: str
    trigger: str
    status: str
    summary: str
    tokens: int
    created_at: str
    steps: list[StepData]


async def recent_runs(session: AsyncSession, *, campaign_id: str, limit: int = 20) -> list[RunData]:
    """The campaign's agent episodes, newest first, each with its ordered steps."""
    runs = list(
        (
            await session.execute(
                select(AgentRun)
                .where(AgentRun.campaign_id == campaign_id)
                .order_by(AgentRun.created_at.desc())
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    out: list[RunData] = []
    for run in runs:
        steps = list(
            (
                await session.execute(
                    select(AgentStep).where(AgentStep.run_id == run.id).order_by(AgentStep.seq)
                )
            )
            .scalars()
            .all()
        )
        out.append(
            RunData(
                id=run.id,
                role=run.role.value,
                trigger=run.trigger,
                status=run.status,
                summary=run.summary,
                tokens=run.tokens,
                created_at=run.created_at.isoformat(),
                steps=[
                    StepData(seq=s.seq, kind=s.kind, tool_name=s.tool_name, content=s.content)
                    for s in steps
                ],
            )
        )
    return out


@dataclass
class FunnelData:
    sourced: int
    contacted: int
    replied: int
    handed_off: int


async def _enrolled_with_message(
    session: AsyncSession,
    *,
    campaign_id: str,
    direction: MessageDirection,
    status: MessageStatus | None = None,
) -> int:
    """Distinct enrollments in the campaign that have a message of the given direction/status."""
    sub = select(Enrollment.id).where(Enrollment.campaign_id == campaign_id)
    stmt = select(func.count(func.distinct(Message.enrollment_id))).where(
        Message.direction == direction, Message.enrollment_id.in_(sub)
    )
    if status is not None:
        stmt = stmt.where(Message.status == status)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def campaign_funnel(session: AsyncSession, *, campaign_id: str) -> FunnelData:
    """Per-campaign funnel: sourced (all enrollments) → contacted → replied → handed_off."""
    sourced = (
        await session.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(Enrollment.campaign_id == campaign_id)
        )
    ).scalar_one()
    handed_off = (
        await session.execute(
            select(func.count())
            .select_from(Enrollment)
            .where(
                Enrollment.campaign_id == campaign_id,
                Enrollment.state == EnrollmentState.handed_off,
            )
        )
    ).scalar_one()
    contacted = await _enrolled_with_message(
        session,
        campaign_id=campaign_id,
        direction=MessageDirection.outbound,
        status=MessageStatus.sent,
    )
    replied = await _enrolled_with_message(
        session, campaign_id=campaign_id, direction=MessageDirection.inbound
    )
    return FunnelData(
        sourced=int(sourced or 0),
        contacted=contacted,
        replied=replied,
        handed_off=int(handed_off or 0),
    )
