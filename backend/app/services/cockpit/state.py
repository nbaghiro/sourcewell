"""State aggregation for the agent experience read-model.

Live snapshot: queue counts, today's throughput, what needs the human, governor headroom per
channel, and per-campaign rollups. Returns a raw dataclass; the HTTP layer maps it to Pydantic.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonObject
from app.models import (
    Campaign,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    Workspace,
)

_IN_SEQUENCE = (
    EnrollmentState.active,
    EnrollmentState.awaiting_approval,
    EnrollmentState.scheduled,
    EnrollmentState.awaiting_reply,
)
_DEFAULT_CAPS = {"email": 120, "linkedin": 80}


@dataclass(frozen=True)
class GovernorChannelData:
    cap: int
    sent: int
    blocked: bool


@dataclass(frozen=True)
class AgentCampaignData:
    id: str
    name: str
    status: str
    active: int


@dataclass(frozen=True)
class StateData:
    status: str  # active | idle
    counts: dict[str, int]
    today: dict[str, int]
    needs_you: dict[str, int]
    governor: dict[str, GovernorChannelData]
    campaigns: list[AgentCampaignData]
    in_sequence: int


async def aggregate_state(session: AsyncSession, *, workspace_id: str) -> StateData:
    """Queue counts, today's throughput, what needs the human, governor headroom."""
    ws = workspace_id
    start = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)

    async def n(stmt: Select[tuple[int]]) -> int:
        return int((await session.execute(stmt)).scalar_one())

    by_state: dict[str, int] = {
        s.value: c
        for s, c in (
            await session.execute(
                select(Enrollment.state, func.count())
                .where(Enrollment.workspace_id == ws)
                .group_by(Enrollment.state)
            )
        )
        .tuples()
        .all()
    }
    counts = {s.value: int(by_state.get(s.value, 0)) for s in EnrollmentState}

    sent_today = await n(
        select(func.count())
        .select_from(Message)
        .where(
            Message.workspace_id == ws,
            Message.status == MessageStatus.sent,
            Message.sent_at >= start,
        )
    )
    replies_today = await n(
        select(func.count())
        .select_from(Message)
        .where(
            Message.workspace_id == ws,
            Message.direction == MessageDirection.inbound,
            Message.created_at >= start,
        )
    )
    handed_today = await n(
        select(func.count())
        .select_from(Enrollment)
        .where(
            Enrollment.workspace_id == ws,
            Enrollment.state == EnrollmentState.handed_off,
            Enrollment.updated_at >= start,
        )
    )

    hot = await n(
        select(func.count())
        .select_from(Enrollment)
        .where(Enrollment.workspace_id == ws, Enrollment.reply_pending.is_(True))
    )

    # Governor headroom per channel (sent today vs the workspace cap).
    ws_obj = await session.get(Workspace, ws)
    settings: JsonObject = (ws_obj.settings if ws_obj else {}) or {}
    governor: dict[str, GovernorChannelData] = {}
    for ch in ("email", "linkedin"):
        raw_cap = settings.get(f"daily_cap_{ch}", _DEFAULT_CAPS[ch])
        cap = int(raw_cap) if isinstance(raw_cap, int | float | str) else _DEFAULT_CAPS[ch]
        used = await n(
            select(func.count())
            .select_from(Message)
            .where(
                Message.workspace_id == ws,
                Message.channel == ch,
                Message.status == MessageStatus.sent,
                Message.sent_at >= start,
            )
        )
        governor[ch] = GovernorChannelData(cap=cap, sent=used, blocked=used >= cap)

    in_seq = sum(counts[s.value] for s in _IN_SEQUENCE)
    cam_rows = (
        (
            await session.execute(
                select(Campaign).where(Campaign.workspace_id == ws).order_by(Campaign.created_at)
            )
        )
        .scalars()
        .all()
    )
    queued_by = {
        cid: c
        for cid, c in (
            await session.execute(
                select(Enrollment.campaign_id, func.count())
                .where(Enrollment.workspace_id == ws, Enrollment.state.in_(_IN_SEQUENCE))
                .group_by(Enrollment.campaign_id)
            )
        )
        .tuples()
        .all()
    }
    campaigns = [
        AgentCampaignData(
            id=c.id,
            name=c.name,
            status=c.status.value,
            active=int(queued_by.get(c.id, 0)),
        )
        for c in cam_rows
    ]

    return StateData(
        status="active" if in_seq > 0 else "idle",
        counts=counts,
        today={"sent": sent_today, "replies": replies_today, "handed_off": handed_today},
        needs_you={"approvals": counts["awaiting_approval"], "hot_replies": hot},
        governor=governor,
        campaigns=campaigns,
        in_sequence=in_seq,
    )
