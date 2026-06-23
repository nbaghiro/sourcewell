"""Campaigns: CRUD service + endpoints (create/list/get, rank into proposed enrollments)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonList, JsonObject
from app.insights import audit
from app.models import (
    AutonomyMode,
    Campaign,
    CampaignStatus,
    Contact,
    Enrollment,
    EnrollmentState,
)
from app.people.sourcing import service as sourcing_service
from app.people.sourcing.agents import evaluate_llm
from app.targeting import FIT_THRESHOLD, Targeting, evaluate
from app.workspace.tenancy import ContextDep, SessionDep, require_workspace

router = APIRouter(prefix="/campaigns", tags=["campaigns"])


# --- Service -----------------------------------------------------------------


async def create_campaign(
    session: AsyncSession,
    *,
    workspace_id: str,
    name: str,
    criteria: JsonObject,
    sequence: JsonList,
    autonomy_mode: AutonomyMode,
    from_email: str | None,
) -> Campaign:
    campaign = Campaign(
        workspace_id=workspace_id,
        name=name,
        status=CampaignStatus.active,
        autonomy_mode=autonomy_mode,
        from_email=from_email,
        criteria=criteria,
        sequence=sequence,
    )
    session.add(campaign)
    await session.flush()
    return campaign


async def get_campaign(session: AsyncSession, *, workspace_id: str, campaign_id: str) -> Campaign:
    campaign = await session.get(Campaign, campaign_id)
    if campaign is None or campaign.workspace_id != workspace_id:
        raise HTTPException(status_code=404, detail="campaign not found")
    return campaign


async def list_campaigns(session: AsyncSession, *, workspace_id: str) -> list[Campaign]:
    rows = await session.execute(
        select(Campaign).where(Campaign.workspace_id == workspace_id).order_by(Campaign.created_at)
    )
    return list(rows.scalars().all())


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
    autonomy_mode: AutonomyMode = AutonomyMode.approve_each
    from_email: str | None = None


class CampaignOut(BaseModel):
    id: str
    name: str
    status: str
    autonomy_mode: str
    from_email: str | None
    criteria: JsonObject
    sequence: JsonList


class EnrollmentOut(BaseModel):
    id: str
    campaign_id: str
    contact_id: str
    state: str
    score: int
    score_rationale: str | None
    current_step: int
    next_run_at: str | None
    outcome: str | None


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
        status=c.status.value,
        autonomy_mode=c.autonomy_mode.value,
        from_email=c.from_email,
        criteria=c.criteria,
        sequence=c.sequence,
    )


def dump_enrollment(e: Enrollment) -> EnrollmentOut:
    return EnrollmentOut(
        id=e.id,
        campaign_id=e.campaign_id,
        contact_id=e.contact_id,
        state=e.state.value,
        score=e.score,
        score_rationale=e.score_rationale,
        current_step=e.current_step,
        next_run_at=e.next_run_at.isoformat() if e.next_run_at else None,
        outcome=e.outcome,
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
        autonomy_mode=body.autonomy_mode,
        from_email=body.from_email,
    )
    await audit.record(
        session,
        ctx,
        action="campaign.created",
        summary=f"Created campaign “{campaign.name}”",
        target_type="campaign",
        target_id=campaign.id,
    )
    return dump(campaign)


@router.get("", response_model=list[CampaignOut])
async def list_campaigns_endpoint(ctx: ContextDep, session: SessionDep) -> list[CampaignOut]:
    ws = require_workspace(ctx)
    return [dump(c) for c in await list_campaigns(session, workspace_id=ws)]


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
    autonomy_mode: AutonomyMode | None = None
    from_email: str | None = None
    status: CampaignStatus | None = None


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
    if body.autonomy_mode is not None:
        campaign.autonomy_mode = body.autonomy_mode
    if body.from_email is not None:
        campaign.from_email = body.from_email
    if body.status is not None:
        campaign.status = body.status
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


@router.post("/{campaign_id}/duplicate", response_model=CampaignOut)
async def duplicate_campaign(campaign_id: str, ctx: ContextDep, session: SessionDep) -> CampaignOut:
    ws = require_workspace(ctx)
    src = await get_campaign(session, workspace_id=ws, campaign_id=campaign_id)
    copy = Campaign(
        workspace_id=ws,
        name=f"{src.name} (copy)",
        status=CampaignStatus.draft,
        autonomy_mode=src.autonomy_mode,
        from_email=src.from_email,
        criteria=dict(src.criteria or {}),
        sequence=list(src.sequence or []),
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
        ctx,
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
    rows = (await session.execute(stmt)).all()
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
