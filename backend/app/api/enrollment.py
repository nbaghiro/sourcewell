"""Enrollment HTTP layer: routes, schemas, serializers (approve / hand off / opt out)."""

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.models import (
    Contact,
    Enrollment,
    EnrollmentState,
    SuppressionReason,
)
from app.services.insights import audit
from app.services.outreach.enrollment import approve_enrollment
from app.services.sourcing import suppression

router = APIRouter(prefix="/enrollments", tags=["enrollments"])


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
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
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
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
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
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
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
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="enrollment.opted_out",
        summary="Marked a candidate not interested",
        target_type="enrollment",
        target_id=enrollment_id,
    )
    return _dump(enrollment)
