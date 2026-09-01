"""Campaigns HTTP layer: routes, request/response schemas, serializers."""

import re
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.core.types import JsonList, JsonObject
from app.models import (
    Authorship,
    AutonomyLevel,
    Campaign,
    CampaignStatus,
    Connection,
    Contact,
    Enrollment,
    EnrollmentState,
    Membership,
    User,
)
from app.services.insights import audit
from app.services.outreach.campaigns import (
    create_campaign,
    get_campaign,
    list_campaigns,
)
from app.services.sourcing import ranking as sourcing_service
from app.services.sourcing.scoring import evaluate_llm
from app.targeting import FIT_THRESHOLD, Targeting, evaluate

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s.]+(\.[^@\s.]+)+$")


async def _validated_from_email(session: SessionDep, ctx: ContextDep, address: str) -> str:
    """The campaign's From address, once we've confirmed the caller may send as it.

    This lands verbatim in the `From` header on the SMTP path, and nothing checked either its
    shape or its domain — so any workspace member could send mail claiming to be any address at
    all, including one belonging to another customer.

    The domain has to be one an organization member already signs in with. There is no
    domain-verification flow to lean on yet, and that is the strongest claim available today: a
    tenant can use `recruiting@` on their own domain, and cannot use anybody else's.
    """
    value = address.strip()
    if not _EMAIL_RE.match(value):
        raise HTTPException(status_code=422, detail="from_email must be a valid email address")
    domain = value.rsplit("@", 1)[1].lower()
    rows = (
        (
            await session.execute(
                select(User.email)
                .join(Membership, Membership.user_id == User.id)
                .where(Membership.organization_id == ctx.org_id)
            )
        )
        .scalars()
        .all()
    )
    allowed = {e.rsplit("@", 1)[-1].lower() for e in rows if e and "@" in e}
    if domain not in allowed:
        raise HTTPException(
            status_code=422,
            detail=f"you can only send from a domain your team uses — {domain} isn't one of them",
        )
    return value


async def _owned_seat(session: SessionDep, ctx: ContextDep, seat_id: str) -> str:
    """The seat id, once we've confirmed it belongs to the caller's organization.

    `seat_id` names the connected account a campaign sends from, and `resolve_channel_seat` loads
    it by primary key alone — it validates the seat's provider and health, never its tenant. So
    without this check a campaign could name *any* `Connection` row in the database and send from
    it: another customer's LinkedIn profile or mailbox, spending their InMail credits and
    receiving the candidate's reply. 404 rather than 403, so the endpoint doesn't confirm that an
    id belongs to somebody else.
    """
    seat = await session.get(Connection, seat_id)
    if seat is None or seat.organization_id != ctx.org_id:
        raise HTTPException(status_code=404, detail="seat not found")
    return seat.id


# --- Schemas -----------------------------------------------------------------


class SequenceStep(BaseModel):
    channel: str = "email"
    delay_days: int = 0
    subject: str | None = None
    body: str | None = None


class CampaignIn(BaseModel):
    name: str
    criteria: Targeting = Targeting()
    sequence: list[SequenceStep] = []
    autonomy_level: AutonomyLevel | None = None
    authored_by: Authorship = Authorship.human
    objective: str | None = None
    seed_contact_ids: list[str] = []
    from_email: str | None = None
    seat_id: str | None = None
    # Send the LinkedIn touchpoints as InMail. Off by default: InMail spends the seat's finite
    # LinkedIn credits and bills at a higher weight, so it is never the silent default.
    use_inmail: bool = False


class CampaignOut(BaseModel):
    id: str
    name: str
    status: CampaignStatus
    from_email: str | None
    criteria: JsonObject
    sequence: JsonList
    # Agent-native fields (the cockpit reads these).
    objective: str | None
    autonomy_level: AutonomyLevel
    authored_by: Authorship
    use_inmail: bool
    field_owners: JsonObject
    next_source_at: str | None
    seat_id: str | None
    created_by_user_id: str | None


class CampaignCounts(BaseModel):
    """Pipeline rollup shown on each campaign list row."""

    sourced: int
    in_sequence: int
    handed_off: int
    needs_you: int


class CampaignRowOut(CampaignOut):
    counts: CampaignCounts


class EnrollmentOut(BaseModel):
    id: str
    # Null on a direct conversation — a one-to-one thread with no sequence behind it.
    campaign_id: str | None
    contact_id: str
    state: EnrollmentState
    score: int
    score_rationale: str | None
    current_step: int
    next_run_at: str | None
    outcome: str | None
    # They answered and it wasn't a clear yes or no, so the ball is with the recruiter. Its own
    # flag rather than a state because the enrollment is still mid-sequence: `state` stays
    # `awaiting_reply` (what the next touchpoint is gated on), which alone reads as "waiting
    # on them" long after they've written back.
    reply_pending: bool


class EnrollmentRowOut(EnrollmentOut):
    contact_name: str
    contact_title: str | None
    contact_company: str | None
    contact_avatar: str | None


class RankOut(BaseModel):
    proposed: int
    enrollments: list[EnrollmentOut]


class EstimateOut(BaseModel):
    total: int
    matches: int


class DeleteOut(BaseModel):
    status: str
    id: str


def dump(c: Campaign) -> CampaignOut:
    return CampaignOut(
        id=c.id,
        name=c.name,
        status=c.status,
        from_email=c.from_email,
        criteria=c.criteria,
        sequence=c.sequence,
        objective=c.objective,
        autonomy_level=c.autonomy_level,
        authored_by=c.authored_by,
        use_inmail=c.use_inmail,
        field_owners=c.field_owners,
        next_source_at=c.next_source_at.isoformat() if c.next_source_at else None,
        seat_id=c.seat_id,
        created_by_user_id=c.created_by_user_id,
    )


def dump_enrollment(e: Enrollment) -> EnrollmentOut:
    return EnrollmentOut(
        id=e.id,
        campaign_id=e.campaign_id,
        contact_id=e.contact_id,
        state=e.state,
        score=e.score,
        score_rationale=e.score_rationale,
        current_step=e.current_step,
        next_run_at=e.next_run_at.isoformat() if e.next_run_at else None,
        outcome=e.outcome,
        reply_pending=e.reply_pending,
    )


# --- Endpoints ---------------------------------------------------------------


@router.post("", response_model=CampaignOut)
async def create_campaign_endpoint(
    body: CampaignIn, ctx: ContextDep, session: SessionDep
) -> CampaignOut:
    ws = require_workspace(ctx)
    campaign = await create_campaign(
        session,
        workspace_id=ws,
        name=body.name,
        criteria=body.criteria.model_dump(),
        sequence=[s.model_dump() for s in body.sequence],
        autonomy_level=body.autonomy_level,
        authored_by=body.authored_by,
        objective=body.objective,
        seed_contact_ids=body.seed_contact_ids,
        from_email=await _validated_from_email(session, ctx, body.from_email)
        if body.from_email
        else None,
        created_by_user_id=ctx.user_id,
        seat_id=await _owned_seat(session, ctx, body.seat_id) if body.seat_id else None,
        use_inmail=body.use_inmail,
    )
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="campaign.created",
        summary=f"Created campaign “{campaign.name}”",
        target_type="campaign",
        target_id=campaign.id,
    )
    return dump(campaign)


_IN_SEQUENCE = (
    EnrollmentState.active,
    EnrollmentState.awaiting_approval,
    EnrollmentState.scheduled,
    EnrollmentState.awaiting_reply,
)


@router.get("", response_model=list[CampaignRowOut])
async def list_campaigns_endpoint(ctx: ContextDep, session: SessionDep) -> list[CampaignRowOut]:
    ws = require_workspace(ctx)
    campaigns = await list_campaigns(session, workspace_id=ws)

    # One pass: count enrollments per (campaign, state), then roll up into the funnel.
    rows = (
        (
            await session.execute(
                select(Enrollment.campaign_id, Enrollment.state, func.count())
                .where(Enrollment.workspace_id == ws)
                .group_by(Enrollment.campaign_id, Enrollment.state)
            )
        )
        .tuples()
        .all()
    )
    by_campaign: dict[str, dict[EnrollmentState, int]] = {}
    for cid, state, cnt in rows:
        if cid is not None:  # direct conversations belong to no campaign
            by_campaign.setdefault(cid, {})[state] = int(cnt)

    def counts_for(cid: str) -> CampaignCounts:
        d = by_campaign.get(cid, {})
        return CampaignCounts(
            sourced=sum(d.values()),
            in_sequence=sum(d.get(s, 0) for s in _IN_SEQUENCE),
            handed_off=d.get(EnrollmentState.handed_off, 0),
            needs_you=d.get(EnrollmentState.awaiting_approval, 0),
        )

    return [CampaignRowOut(**dump(c).model_dump(), counts=counts_for(c.id)) for c in campaigns]


@router.get("/{campaign_id}", response_model=CampaignOut)
async def get_campaign_endpoint(
    campaign_id: str, ctx: ContextDep, session: SessionDep
) -> CampaignOut:
    ws = require_workspace(ctx)
    return dump(await get_campaign(session, workspace_id=ws, campaign_id=campaign_id))


class CampaignPatch(BaseModel):
    name: str | None = None
    criteria: Targeting | None = None
    sequence: list[SequenceStep] | None = None
    autonomy_level: AutonomyLevel | None = None
    objective: str | None = None
    from_email: str | None = None
    use_inmail: bool | None = None
    status: CampaignStatus | None = None
    seat_id: str | None = None


@router.patch("/{campaign_id}", response_model=CampaignOut)
async def update_campaign(
    campaign_id: str, body: CampaignPatch, ctx: ContextDep, session: SessionDep
) -> CampaignOut:
    ws = require_workspace(ctx)
    campaign = await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    if body.name is not None:
        campaign.name = body.name
    if body.criteria is not None:
        campaign.criteria = body.criteria.model_dump()
    if body.sequence is not None:
        campaign.sequence = [s.model_dump() for s in body.sequence]
    if body.autonomy_level is not None:
        campaign.autonomy_level = body.autonomy_level
    if body.use_inmail is not None:
        campaign.use_inmail = body.use_inmail
    if body.objective is not None:
        campaign.objective = body.objective
    if body.from_email is not None:
        campaign.from_email = await _validated_from_email(session, ctx, body.from_email)
    if body.status is not None:
        campaign.status = body.status
    if body.seat_id is not None:
        campaign.seat_id = await _owned_seat(session, ctx, body.seat_id)
    await session.flush()
    return dump(campaign)


async def _set_status(
    session: SessionDep, ws: str, campaign_id: str, status: CampaignStatus
) -> CampaignOut:
    campaign = await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    campaign.status = status
    await session.flush()
    return dump(campaign)


@router.post("/{campaign_id}/pause", response_model=CampaignOut)
async def pause_campaign(campaign_id: str, ctx: ContextDep, session: SessionDep) -> CampaignOut:
    return await _set_status(session, require_workspace(ctx), campaign_id, CampaignStatus.paused)


@router.post("/{campaign_id}/resume", response_model=CampaignOut)
async def resume_campaign(campaign_id: str, ctx: ContextDep, session: SessionDep) -> CampaignOut:
    return await _set_status(session, require_workspace(ctx), campaign_id, CampaignStatus.active)


@router.post("/{campaign_id}/archive", response_model=CampaignOut)
async def archive_campaign(campaign_id: str, ctx: ContextDep, session: SessionDep) -> CampaignOut:
    return await _set_status(session, require_workspace(ctx), campaign_id, CampaignStatus.done)


@router.post("/{campaign_id}/source", response_model=CampaignOut)
async def source_now(campaign_id: str, ctx: ContextDep, session: SessionDep) -> CampaignOut:
    """Queue an immediate sourcing pass — the worker's next tick (~10s) runs the Sourcing agent."""
    ws = require_workspace(ctx)
    campaign = await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    campaign.next_source_at = datetime.now(UTC)
    await session.flush()
    return dump(campaign)


@router.post("/{campaign_id}/duplicate", response_model=CampaignOut)
async def duplicate_campaign(campaign_id: str, ctx: ContextDep, session: SessionDep) -> CampaignOut:
    ws = require_workspace(ctx)
    src = await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    copy = Campaign(
        workspace_id=ws,
        name=f"{src.name} (copy)",
        status=CampaignStatus.draft,
        autonomy_level=src.autonomy_level,
        authored_by=src.authored_by,
        objective=src.objective,
        from_email=src.from_email,
        criteria=dict(src.criteria or {}),
        sequence=list(src.sequence or []),
        seat_id=src.seat_id,
        created_by_user_id=ctx.user_id,
        use_inmail=src.use_inmail,
        constraints=dict(src.constraints or {}),
        field_owners=dict(src.field_owners or {}),
    )
    session.add(copy)
    await session.flush()
    return dump(copy)


@router.delete("/{campaign_id}", response_model=DeleteOut)
async def delete_campaign(campaign_id: str, ctx: ContextDep, session: SessionDep) -> DeleteOut:
    ws = require_workspace(ctx)
    campaign = await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    await session.delete(campaign)
    await session.flush()
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="campaign.deleted",
        summary="Deleted a campaign",
        target_type="campaign",
        target_id=campaign_id,
    )
    return DeleteOut(status="deleted", id=campaign_id)


@router.get("/{campaign_id}/estimate", response_model=EstimateOut)
async def estimate_audience(campaign_id: str, ctx: ContextDep, session: SessionDep) -> EstimateOut:
    """How many workspace contacts the evaluator considers a match for this campaign's criteria."""
    ws = require_workspace(ctx)
    campaign = await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    contacts = (
        (await session.execute(select(Contact).where(Contact.workspace_id == ws))).scalars().all()
    )
    matches = sum(1 for c in contacts if evaluate(c, campaign.criteria or {})[0] >= FIT_THRESHOLD)
    return EstimateOut(total=len(contacts), matches=matches)


@router.post("/{campaign_id}/rank", response_model=RankOut)
async def rank_campaign(campaign_id: str, ctx: ContextDep, session: SessionDep) -> RankOut:
    ws = require_workspace(ctx)
    campaign = await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    proposed = await sourcing_service.rank_campaign(session, workspace_id=ws, campaign=campaign)
    return RankOut(proposed=len(proposed), enrollments=[dump_enrollment(e) for e in proposed])


class EnrollRequest(BaseModel):
    contact_id: str


@router.post("/{campaign_id}/enroll", response_model=EnrollmentOut)
async def enroll_contact(
    campaign_id: str, body: EnrollRequest, ctx: ContextDep, session: SessionDep
) -> EnrollmentOut:
    """Add a single contact to a campaign as a scored, proposed enrollment."""
    ws = require_workspace(ctx)
    campaign = await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    contact = await session.get(Contact, body.contact_id)
    if contact is None or contact.workspace_id != ws:
        raise HTTPException(status_code=404, detail="contact not found")
    existing = (
        await session.execute(
            select(Enrollment).where(
                Enrollment.campaign_id == campaign_id, Enrollment.contact_id == body.contact_id
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return dump_enrollment(existing)
    score, rationale = await evaluate_llm(contact, campaign.criteria or {})
    enrollment = Enrollment(
        workspace_id=ws,
        campaign_id=campaign_id,
        contact_id=body.contact_id,
        state=EnrollmentState.proposed,
        score=score,
        score_rationale=rationale,
    )
    session.add(enrollment)
    await session.flush()
    return dump_enrollment(enrollment)


@router.get("/{campaign_id}/enrollments", response_model=list[EnrollmentRowOut])
async def list_enrollments(
    campaign_id: str,
    ctx: ContextDep,
    session: SessionDep,
    state: Annotated[EnrollmentState | None, Query()] = None,
) -> list[EnrollmentRowOut]:
    ws = require_workspace(ctx)
    await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    stmt = (
        select(Enrollment, Contact)
        .join(Contact, Enrollment.contact_id == Contact.id)
        .where(Enrollment.campaign_id == campaign_id)
        .order_by(Enrollment.score.desc())
    )
    if state is not None:
        stmt = stmt.where(Enrollment.state == state)
    rows = (await session.execute(stmt)).tuples().all()
    return [
        EnrollmentRowOut(
            **dump_enrollment(e).model_dump(),
            contact_name=c.full_name,
            contact_title=c.title,
            contact_company=c.company,
            contact_avatar=c.avatar_url,
        )
        for e, c in rows
    ]
