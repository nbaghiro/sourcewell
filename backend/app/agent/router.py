"""The agent experience read-model + chat — a unified, humanized view of what the agent is doing.

Synthesized from what we already persist (enrollments, messages, governor settings) — no new
tables. Every agent-experience UI variant (activity feed, mission control, daily briefing, copilot
chat) renders off these endpoints, so the comparison is on UX, not data.
"""

from datetime import UTC, datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import Select, func, select

from app.core import llm
from app.core.types import JsonObject
from app.models import (
    Campaign,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    Workspace,
)
from app.people import suppression
from app.people.sourcing import people
from app.people.sourcing.adapters.registry import build_providers_for_org
from app.targeting import Targeting
from app.workspace.tenancy import ContextDep, SessionDep, require_workspace

router = APIRouter(prefix="/agent", tags=["agent"])

_IN_SEQUENCE = (
    EnrollmentState.active,
    EnrollmentState.awaiting_approval,
    EnrollmentState.scheduled,
    EnrollmentState.awaiting_reply,
)
_DEFAULT_CAPS = {"email": 120, "linkedin": 80}


# ---- shapes ----------------------------------------------------------------


class Ref(BaseModel):
    id: str
    name: str
    sub: str | None = None
    avatar: str | None = None


class ActivityEvent(BaseModel):
    id: str
    ts: str
    kind: str  # sourced | drafted | scheduled | sent | reply | handed_off | opted_out | completed
    title: str
    detail: str | None = None
    rationale: str | None = None
    contact: Ref | None = None
    campaign: Ref | None = None


class GovernorChannel(BaseModel):
    cap: int
    sent: int
    blocked: bool


class AgentCampaign(BaseModel):
    id: str
    name: str
    status: str
    active: int


class AgentState(BaseModel):
    status: str  # active | idle
    counts: dict[str, int]
    today: dict[str, int]
    needs_you: dict[str, int]
    governor: dict[str, GovernorChannel]
    campaigns: list[AgentCampaign]


class ChatIn(BaseModel):
    message: str


class ChatOut(BaseModel):
    reply: str
    kind: str  # status | explain | find | help
    data: JsonObject | None = None


def _contact_ref(c: Contact) -> Ref:
    return Ref(id=c.id, name=c.full_name, sub=c.title, avatar=c.avatar_url)


def _snippet(text: str, n: int = 90) -> str:
    text = " ".join((text or "").split())
    return (text[:n] + "…") if len(text) > n else text


# ---- activity --------------------------------------------------------------


@router.get("/activity", response_model=list[ActivityEvent])
async def activity(ctx: ContextDep, session: SessionDep, limit: int = 40) -> list[ActivityEvent]:
    """A merged, humanized stream of the agent's recent actions, newest first."""
    ws = require_workspace(ctx)
    events: list[tuple[datetime, ActivityEvent]] = []

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
        ref_c, ref_cam = _contact_ref(ct), Ref(id=cam.id, name=cam.name)
        if m.direction == MessageDirection.inbound:
            ts = m.created_at
            events.append(
                (
                    ts,
                    ActivityEvent(
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
                    ActivityEvent(
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
                    ActivityEvent(
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
                    ActivityEvent(
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
        ref_c, ref_cam = _contact_ref(ct), Ref(id=cam.id, name=cam.name)
        if e.state == EnrollmentState.proposed:
            ts = e.created_at
            events.append(
                (
                    ts,
                    ActivityEvent(
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
                    ActivityEvent(
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


# ---- state -----------------------------------------------------------------


@router.get("/state", response_model=AgentState)
async def state(ctx: ContextDep, session: SessionDep) -> AgentState:
    """Live snapshot: queue counts, today's throughput, what needs the human, governor headroom."""
    ws = require_workspace(ctx)
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
    governor: dict[str, GovernorChannel] = {}
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
        governor[ch] = GovernorChannel(cap=cap, sent=used, blocked=used >= cap)

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
        AgentCampaign(
            id=c.id,
            name=c.name,
            status=c.status.value,
            active=int(queued_by.get(c.id, 0)),
        )
        for c in cam_rows
    ]

    return AgentState(
        status="active" if in_seq > 0 else "idle",
        counts=counts,
        today={"sent": sent_today, "replies": replies_today, "handed_off": handed_today},
        needs_you={"approvals": counts["awaiting_approval"], "hot_replies": hot},
        governor=governor,
        campaigns=campaigns,
    )


# ---- chat ------------------------------------------------------------------

_INTENTS = {"status", "explain", "find", "help"}


async def _classify(message: str) -> JsonObject:
    if llm.is_enabled():
        obj = await llm.complete_json(
            "Classify a user's message to an autonomous outreach agent.",
            f"Message: {message!r}\n"
            'Return JSON {"intent": one of ["status","explain","find","help"], '
            '"subject": the person/campaign/criteria mentioned or null}.',
            max_tokens=80,
        )
        if obj and obj.get("intent") in _INTENTS:
            return obj
    m = message.lower()
    if any(w in m for w in ("why", "explain", "about ", "tell me about", "skip")):
        return {"intent": "explain"}
    if any(w in m for w in ("find", "source", "search", "look for", "get me", "prospect")):
        return {"intent": "find"}
    if any(w in m for w in ("help", "what can you", "how do you")):
        return {"intent": "help"}
    return {"intent": "status"}


@router.post("/chat", response_model=ChatOut)
async def chat(body: ChatIn, ctx: ContextDep, session: SessionDep) -> ChatOut:
    """A bounded copilot: answers about state, explains a person, previews a search. No destructive
    actions yet — those are a fast-follow once the chat direction is validated."""
    ws = require_workspace(ctx)
    parsed = await _classify(body.message)
    intent = parsed["intent"]

    if intent == "help":
        return ChatOut(
            kind="help",
            reply="I'm your sourcing agent. Ask me things like: “what needs me today?”, "
            "“why did you skip <name>?”, or “find VPs of Sales in EU fintech”. "
            "I draft, send, and watch for replies on autopilot within your guardrails.",
            data=None,
        )

    if intent == "explain":
        contacts = (
            (await session.execute(select(Contact).where(Contact.workspace_id == ws)))
            .scalars()
            .all()
        )
        low = body.message.lower()
        hit = next(
            (
                c
                for c in contacts
                if c.full_name.lower() in low
                or any(p and p.lower() in low for p in c.full_name.split())
            ),
            None,
        )
        if hit is None:
            return ChatOut(
                kind="explain",
                reply="Tell me who — e.g. “why did you skip Aisha Park?”",
                data=None,
            )
        if await suppression.is_suppressed(session, organization_id=ctx.org_id, email=hit.email):
            return ChatOut(
                kind="explain",
                reply=f"{hit.full_name} is on your do-not-contact list, so the agent skips them.",
                data={"contact": hit.full_name},
            )
        enr = (
            await session.execute(
                select(Enrollment)
                .where(Enrollment.workspace_id == ws, Enrollment.contact_id == hit.id)
                .limit(1)
            )
        ).scalar_one_or_none()
        if enr is not None:
            why = enr.score_rationale or "fit against the campaign criteria"
            state_label = enr.state.value.replace("_", " ")
            reply = f"{hit.full_name} scored {enr.score}/100 — {why} (currently {state_label})."
            return ChatOut(
                kind="explain",
                reply=reply,
                data={"contact": hit.full_name, "score": enr.score},
            )
        return ChatOut(
            kind="explain",
            reply=f"{hit.full_name} is in Contacts but not yet ranked into a campaign.",
            data={"contact": hit.full_name},
        )

    if intent == "find":
        providers = await build_providers_for_org(session, ctx.org_id)
        targeting = Targeting(keywords=body.message)
        hits = await people.search_people(providers, targeting, limit=15)
        top = ", ".join(h.full_name for h in hits[:3])
        return ChatOut(
            kind="find",
            reply=f"Found {len(hits)} people"
            + (f" — top matches {top}. " if top else ". ")
            + "Open Find People to review and import them.",
            data={"count": len(hits), "names": [h.full_name for h in hits[:6]]},
        )

    # status (default)
    st = await state(ctx, session)
    needs = st.needs_you
    in_seq = sum(st.counts[s.value] for s in _IN_SEQUENCE)
    parts = []
    if needs["approvals"]:
        parts.append(
            f"{needs['approvals']} draft{'s' if needs['approvals'] != 1 else ''} to approve"
        )
    if needs["hot_replies"]:
        parts.append(
            f"{needs['hot_replies']} repl{'ies' if needs['hot_replies'] != 1 else 'y'} waiting"
        )
    head = "You're all caught up — " if not parts else "You have " + " and ".join(parts) + ". "
    reply = (
        head
        + f"The agent has {in_seq} people in sequence and sent {st.today['sent']} today "
        + f"({st.today['replies']} replied, {st.today['handed_off']} handed off)."
    )
    return ChatOut(kind="status", reply=reply, data=st.model_dump())
