"""Activity-stream builders for the agent experience read-model.

A merged, humanized stream of the agent's recent actions (messages + enrollment lifecycle),
synthesized from what we already persist — no new tables. Returns raw dataclasses; the HTTP layer
maps them to Pydantic response models.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Campaign,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
)


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
