"""Agent read-models (insights): the operational views behind Mission Control + the cockpit.

Derived, no-new-state read-models synthesized from what we already persist — the live agent-state
snapshot, the per-campaign run-trace feed + funnel, and a humanized activity stream. The HTTP layer
maps these raw dataclasses to Pydantic response models.
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonObject
from app.models import (
    AgentRun,
    AgentStep,
    Campaign,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    Workspace,
)

# === per-campaign run-trace feed + funnel ====================================


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
    """The campaign's agent runs, newest first, each with its ordered steps."""
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


# === humanized activity stream ===============================================


@dataclass(frozen=True)
class RefData:
    id: str
    name: str
    sub: str | None = None
    avatar: str | None = None


@dataclass(frozen=True)
class ActivityEventData:
    id: str
    ts: str
    kind: str  # sourced | drafted | scheduled | sent | reply | handed_off | opted_out | completed
    title: str
    detail: str | None = None
    rationale: str | None = None
    contact: RefData | None = None
    campaign: RefData | None = None


def _contact_ref(c: Contact) -> RefData:
    return RefData(id=c.id, name=c.full_name, sub=c.title, avatar=c.avatar_url)


def _snippet(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return (text[:n] + "…") if len(text) > n else text


async def build_activity_stream(
    session: AsyncSession, *, workspace_id: str, limit: int
) -> list[ActivityEventData]:
    """A merged, humanized stream of the agent's recent actions, newest first."""
    ws = workspace_id
    events: list[tuple[datetime, ActivityEventData]] = []

    # Messages → drafted / scheduled / sent / reply
    msg_rows = (
        (
            await session.execute(
                select(Message, Contact, Campaign)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .join(Campaign, Enrollment.campaign_id == Campaign.id)
                .where(Message.workspace_id == ws)
                .order_by(Message.created_at.desc())
                .limit(limit * 2)
            )
        )
        .tuples()
        .all()
    )
    for m, ct, cam in msg_rows:
        ref_c, ref_cam = _contact_ref(ct), RefData(id=cam.id, name=cam.name)
        if m.direction == MessageDirection.inbound:
            ts = m.created_at
            events.append(
                (
                    ts,
                    ActivityEventData(
                        id=f"reply:{m.id}",
                        ts=ts.isoformat(),
                        kind="reply",
                        title=f"{ct.full_name} replied",
                        detail=_snippet(m.body),
                        contact=ref_c,
                        campaign=ref_cam,
                    ),
                )
            )
        elif m.status == MessageStatus.sent:
            ts = m.sent_at or m.created_at
            events.append(
                (
                    ts,
                    ActivityEventData(
                        id=f"sent:{m.id}",
                        ts=ts.isoformat(),
                        kind="sent",
                        title=f"Sent {m.channel.value} to {ct.full_name}",
                        detail=m.subject or _snippet(m.body),
                        contact=ref_c,
                        campaign=ref_cam,
                    ),
                )
            )
        elif m.status == MessageStatus.approved:
            ts = m.created_at
            events.append(
                (
                    ts,
                    ActivityEventData(
                        id=f"sched:{m.id}",
                        ts=ts.isoformat(),
                        kind="scheduled",
                        title=f"Queued {m.channel.value} for {ct.full_name}",
                        detail=m.subject or _snippet(m.body),
                        contact=ref_c,
                        campaign=ref_cam,
                    ),
                )
            )
        elif m.status == MessageStatus.draft:
            ts = m.created_at
            events.append(
                (
                    ts,
                    ActivityEventData(
                        id=f"draft:{m.id}",
                        ts=ts.isoformat(),
                        kind="drafted",
                        title=f"Drafted {m.channel.value} for {ct.full_name}",
                        detail=m.subject or _snippet(m.body),
                        contact=ref_c,
                        campaign=ref_cam,
                    ),
                )
            )

    # Enrollments → sourced (proposed) + terminal lifecycle
    enr_rows = (
        (
            await session.execute(
                select(Enrollment, Contact, Campaign)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .join(Campaign, Enrollment.campaign_id == Campaign.id)
                .where(Enrollment.workspace_id == ws)
                .order_by(Enrollment.updated_at.desc())
                .limit(limit * 2)
            )
        )
        .tuples()
        .all()
    )
    terminal = {
        EnrollmentState.handed_off: ("handed_off", "Handed {n} to you — positive reply"),
        EnrollmentState.opted_out: ("opted_out", "{n} opted out — stopped"),
        EnrollmentState.completed: ("completed", "Sequence finished for {n} — no reply"),
    }
    for e, ct, cam in enr_rows:
        ref_c, ref_cam = _contact_ref(ct), RefData(id=cam.id, name=cam.name)
        if e.state == EnrollmentState.proposed:
            ts = e.created_at
            events.append(
                (
                    ts,
                    ActivityEventData(
                        id=f"src:{e.id}",
                        ts=ts.isoformat(),
                        kind="sourced",
                        title=f"Ranked {ct.full_name} for {cam.name}",
                        detail=f"fit {e.score}",
                        rationale=e.score_rationale,
                        contact=ref_c,
                        campaign=ref_cam,
                    ),
                )
            )
        elif e.state in terminal:
            kind, tmpl = terminal[e.state]
            ts = e.updated_at
            events.append(
                (
                    ts,
                    ActivityEventData(
                        id=f"{kind}:{e.id}",
                        ts=ts.isoformat(),
                        kind=kind,
                        title=tmpl.format(n=ct.full_name),
                        contact=ref_c,
                        campaign=ref_cam,
                    ),
                )
            )

    events.sort(key=lambda x: x[0], reverse=True)
    return [ev for _, ev in events[:limit]]


# === live agent-state snapshot ===============================================

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
