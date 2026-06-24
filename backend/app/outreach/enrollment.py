"""Enrollment: the state machine (service) + endpoints (approve / hand off / opt out).

`tick()` advances one enrollment by exactly one transition; the worker (or the admin run-due
endpoint) calls it for every enrollment whose `next_run_at` is due. `state` + `next_run_at` are the
source of truth — no external scheduler.

Flow:
    proposed --approve--> active
    active     -> draft a touch; auto-mode approves it (scheduled) else awaiting_approval
    (message approved) -> scheduled
    scheduled  -> send the approved touch, advance step, wait (awaiting_reply)
    awaiting_reply -> next touch due? back to active : completed
    (inbound reply) -> handed_off (interested) | opted_out  [handled in app.outreach.messaging]
"""

from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.types import JsonList
from app.deps import ContextDep, SessionDep, require_workspace
from app.insights import audit
from app.models import (
    AutonomyMode,
    Campaign,
    Channel,
    Contact,
    Enrollment,
    EnrollmentState,
    Message,
    MessageDirection,
    MessageStatus,
    SuppressionReason,
    Workspace,
)
from app.outreach.messaging import draft_message, send_via_channel
from app.people import suppression
from app.services.outreach import governor

router = APIRouter(prefix="/enrollments", tags=["enrollments"])

_FINAL_GRACE_DAYS = 3
_MAX_SEND_ATTEMPTS = 3
_BACKOFF = (timedelta(minutes=5), timedelta(minutes=15), timedelta(minutes=60))


# --- State machine -----------------------------------------------------------


def _advance(enrollment: Enrollment, sequence: JsonList, now: datetime) -> None:
    """Move to the next touch (or the post-sequence grace wait)."""
    enrollment.current_step += 1
    enrollment.state = EnrollmentState.awaiting_reply
    if enrollment.current_step < len(sequence):
        raw_delay = sequence[enrollment.current_step].get("delay_days", 0)
        delay = int(raw_delay) if isinstance(raw_delay, int | float | str) else 0
        enrollment.next_run_at = now + timedelta(days=delay)
    else:
        enrollment.next_run_at = now + timedelta(days=_FINAL_GRACE_DAYS)


async def approve_enrollment(
    session: AsyncSession, *, workspace_id: str, enrollment_id: str, now: datetime
) -> Enrollment:
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="enrollment not found")
    if enrollment.state != EnrollmentState.proposed:
        raise HTTPException(status_code=409, detail="enrollment is not awaiting approval")
    enrollment.state = EnrollmentState.active
    enrollment.next_run_at = now
    await session.flush()
    return enrollment


async def list_for_campaign(
    session: AsyncSession,
    *,
    campaign_id: str,
    state: EnrollmentState | None = None,
) -> list[Enrollment]:
    stmt = select(Enrollment).where(Enrollment.campaign_id == campaign_id)
    if state is not None:
        stmt = stmt.where(Enrollment.state == state)
    stmt = stmt.order_by(Enrollment.score.desc())
    return list[Enrollment]((await session.execute(stmt)).scalars().all())


async def tick(session: AsyncSession, *, enrollment: Enrollment, now: datetime) -> None:
    campaign = await session.get(Campaign, enrollment.campaign_id)
    contact = await session.get(Contact, enrollment.contact_id)
    if campaign is None or contact is None:
        enrollment.state = EnrollmentState.completed
        enrollment.next_run_at = None
        return
    sequence = campaign.sequence or []

    if enrollment.state == EnrollmentState.active:
        await _draft_touch(session, enrollment, campaign, contact, sequence, now)
    elif enrollment.state == EnrollmentState.scheduled:
        await _send_touch(session, enrollment, campaign, contact, sequence, now)
    elif enrollment.state == EnrollmentState.awaiting_reply:
        if enrollment.current_step < len(sequence):
            enrollment.state = EnrollmentState.active
            enrollment.next_run_at = now
        else:
            enrollment.state = EnrollmentState.completed
            enrollment.next_run_at = None
    await session.flush()


async def _draft_touch(
    session: AsyncSession,
    enrollment: Enrollment,
    campaign: Campaign,
    contact: Contact,
    sequence: JsonList,
    now: datetime,
) -> None:
    if enrollment.current_step >= len(sequence):
        enrollment.state = EnrollmentState.completed
        enrollment.next_run_at = None
        return
    step = sequence[enrollment.current_step]
    channel = Channel.linkedin if step.get("channel") == "linkedin" else Channel.email
    subject, body = await draft_message(contact, step)
    message = Message(
        workspace_id=enrollment.workspace_id,
        enrollment_id=enrollment.id,
        direction=MessageDirection.outbound,
        channel=channel,
        status=MessageStatus.draft,
        subject=subject,
        body=body,
    )
    session.add(message)
    await session.flush()

    if campaign.autonomy_mode == AutonomyMode.auto:
        message.status = MessageStatus.approved
        enrollment.state = EnrollmentState.scheduled
        enrollment.next_run_at = now
    else:
        enrollment.state = EnrollmentState.awaiting_approval
        enrollment.next_run_at = None


async def _send_touch(
    session: AsyncSession,
    enrollment: Enrollment,
    campaign: Campaign,
    contact: Contact,
    sequence: JsonList,
    now: datetime,
) -> None:
    message = (
        (
            await session.execute(
                select(Message)
                .where(
                    Message.enrollment_id == enrollment.id,
                    Message.status == MessageStatus.approved,
                )
                .order_by(Message.created_at.desc())
            )
        )
        .scalars()
        .first()
    )
    if message is None:
        # Nothing approved to send — drop back to waiting for approval.
        enrollment.state = EnrollmentState.awaiting_approval
        enrollment.next_run_at = None
        return

    # Org-level do-not-contact gate: never send to a suppressed address.
    workspace = await session.get(Workspace, enrollment.workspace_id)
    org_id = workspace.organization_id if workspace else None
    if org_id and await suppression.is_suppressed(
        session, organization_id=org_id, email=contact.email
    ):
        message.status = MessageStatus.failed
        enrollment.state = EnrollmentState.opted_out
        enrollment.outcome = "opted_out"
        enrollment.next_run_at = None
        return

    # Rate/window governor: defer (without advancing) if a cap or window blocks the send.
    allowed, retry_at = await governor.can_send_now(
        session, workspace_id=enrollment.workspace_id, channel=message.channel, now=now
    )
    if not allowed:
        enrollment.next_run_at = retry_at or (now + timedelta(minutes=15))
        return

    target = contact.linkedin_url if message.channel == Channel.linkedin else contact.email
    if not target:
        message.status = MessageStatus.failed
        _advance(enrollment, sequence, now)
        return

    sender = campaign.from_email or get_settings().default_from_email
    unsub = suppression.unsubscribe_url(org_id, contact.email) if org_id and contact.email else None
    try:
        await send_via_channel(
            channel=message.channel,
            sender=sender,
            email=contact.email,
            linkedin_url=contact.linkedin_url,
            subject=message.subject or "",
            body=message.body,
            unsubscribe_url=unsub,
        )
        message.status = MessageStatus.sent
        message.sent_at = now
    except Exception:
        # Transient failure: retry with backoff, advancing only after exhausting attempts.
        message.attempts += 1
        if message.attempts < _MAX_SEND_ATTEMPTS:
            enrollment.next_run_at = now + _BACKOFF[min(message.attempts - 1, len(_BACKOFF) - 1)]
            return
        message.status = MessageStatus.failed
        _advance(enrollment, sequence, now)
        return

    _advance(enrollment, sequence, now)


# --- Schemas -----------------------------------------------------------------


class EnrollmentActionOut(BaseModel):
    id: str
    state: str
    outcome: str | None
    next_run_at: str | None


class BulkApproveOut(BaseModel):
    approved: int
    ids: list[str]


def _dump(e: Enrollment) -> EnrollmentActionOut:
    return EnrollmentActionOut(
        id=e.id,
        state=e.state.value,
        outcome=e.outcome,
        next_run_at=e.next_run_at.isoformat() if e.next_run_at else None,
    )


# --- Endpoints ---------------------------------------------------------------


@router.post("/{enrollment_id}/approve", response_model=EnrollmentActionOut)
async def approve_enrollment_endpoint(
    enrollment_id: str, ctx: ContextDep, session: SessionDep
) -> EnrollmentActionOut:
    ws = require_workspace(ctx)
    enrollment = await approve_enrollment(
        session, workspace_id=ws, enrollment_id=enrollment_id, now=datetime.now(UTC)
    )
    await audit.record(
        session,
        ctx,
        action="enrollment.approved",
        summary="Approved a proposed lead",
        target_type="enrollment",
        target_id=enrollment_id,
    )
    return _dump(enrollment)


class BulkApproveRequest(BaseModel):
    ids: list[str]


@router.post("/bulk-approve", response_model=BulkApproveOut)
async def bulk_approve(
    body: BulkApproveRequest, ctx: ContextDep, session: SessionDep
) -> BulkApproveOut:
    """Approve many proposed enrollments at once (replaces the client-side loop)."""
    ws = require_workspace(ctx)
    now = datetime.now(UTC)
    approved: list[str] = []
    for eid in body.ids:
        try:
            await approve_enrollment(session, workspace_id=ws, enrollment_id=eid, now=now)
            approved.append(eid)
        except HTTPException:
            continue
    await audit.record(
        session,
        ctx,
        action="enrollment.approved",
        summary=f"Approved {len(approved)} leads in bulk",
        target_type="enrollment",
    )
    return BulkApproveOut(approved=len(approved), ids=approved)


async def _set_terminal(
    session: SessionDep, ws: str, enrollment_id: str, *, state: EnrollmentState, outcome: str
) -> Enrollment:
    enrollment = await session.get(Enrollment, enrollment_id)
    if enrollment is None or enrollment.workspace_id != ws:
        raise HTTPException(status_code=404, detail="enrollment not found")
    enrollment.state = state
    enrollment.outcome = outcome
    enrollment.next_run_at = None
    enrollment.reply_pending = False
    await session.flush()
    return enrollment


@router.post("/{enrollment_id}/handoff", response_model=EnrollmentActionOut)
async def hand_off(enrollment_id: str, ctx: ContextDep, session: SessionDep) -> EnrollmentActionOut:
    """Hand an interested candidate to the hiring team."""
    ws = require_workspace(ctx)
    enrollment = await _set_terminal(
        session, ws, enrollment_id, state=EnrollmentState.handed_off, outcome="interested"
    )
    await audit.record(
        session,
        ctx,
        action="enrollment.handed_off",
        summary="Handed off a candidate",
        target_type="enrollment",
        target_id=enrollment_id,
    )
    return _dump(enrollment)


@router.post("/{enrollment_id}/opt-out", response_model=EnrollmentActionOut)
async def opt_out(enrollment_id: str, ctx: ContextDep, session: SessionDep) -> EnrollmentActionOut:
    """Mark a candidate as not interested / opted out."""
    ws = require_workspace(ctx)
    enrollment = await _set_terminal(
        session, ws, enrollment_id, state=EnrollmentState.opted_out, outcome="opted_out"
    )
    # Add the contact to the org's do-not-contact list so they're never re-contacted.
    contact = await session.get(Contact, enrollment.contact_id)
    if contact is not None and contact.email:
        await suppression.suppress(
            session,
            organization_id=ctx.org_id,
            email=contact.email,
            reason=SuppressionReason.opted_out,
            contact_id=contact.id,
        )
    await audit.record(
        session,
        ctx,
        action="enrollment.opted_out",
        summary="Marked a candidate not interested",
        target_type="enrollment",
        target_id=enrollment_id,
    )
    return _dump(enrollment)
