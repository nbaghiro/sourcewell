"""Contacts HTTP layer: import / sample / list-and-detail endpoints and schemas."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select

from app.api.context import ContextDep, SessionDep
from app.api.guards import require_workspace
from app.models import (
    Campaign,
    Contact,
    Enrollment,
    Message,
    MessageDirection,
    MessageStatus,
    SuppressionReason,
)
from app.services.insights import audit
from app.services.sourcing import suppression
from app.services.sourcing.contacts import create_contacts, generate_sample

router = APIRouter(prefix="/contacts", tags=["contacts"])


# --- Schemas -----------------------------------------------------------------


class ContactIn(BaseModel):
    full_name: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    avatar_url: str | None = None
    skills: list[str] = []
    notes: str | None = None
    tags: list[str] = []
    company_size: str | None = None
    industry: str | None = None


class ImportRequest(BaseModel):
    contacts: list[ContactIn]


class SampleRequest(BaseModel):
    count: int = 5


class ContactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    full_name: str
    title: str | None
    company: str | None
    location: str | None
    email: str | None
    email_status: str
    linkedin_url: str | None
    avatar_url: str | None
    skills: list[str]
    source: str
    notes: str | None
    tags: list[str]
    company_size: str | None
    industry: str | None


class ImportOut(BaseModel):
    created: int
    contacts: list[ContactOut]


class ContactEnrollmentOut(BaseModel):
    id: str
    campaign_id: str
    campaign_name: str
    state: str
    score: int
    current_step: int


class ContactActivityOut(BaseModel):
    id: str
    direction: str
    channel: str
    status: str
    subject: str | None
    body: str
    created_at: str | None
    scheduled_at: str | None
    campaign_name: str


class ContactStatsOut(BaseModel):
    best_score: int
    campaigns: int
    replies: int
    last_activity_at: str | None


class ContactDetailOut(ContactOut):
    enrollments: list[ContactEnrollmentOut]
    activity: list[ContactActivityOut]
    stats: ContactStatsOut


class DeleteOut(BaseModel):
    status: str
    id: str


class ContactPatch(BaseModel):
    full_name: str | None = None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    email: str | None = None
    linkedin_url: str | None = None
    skills: list[str] | None = None
    notes: str | None = None
    tags: list[str] | None = None
    company_size: str | None = None
    industry: str | None = None


def dump(c: Contact) -> ContactOut:
    return ContactOut.model_validate(c)


# --- Endpoints ---------------------------------------------------------------


@router.post("/import", response_model=ImportOut)
async def import_contacts(body: ImportRequest, ctx: ContextDep, session: SessionDep) -> ImportOut:
    ws = require_workspace(ctx)
    created = await create_contacts(
        session, workspace_id=ws, items=[c.model_dump() for c in body.contacts]
    )
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="contact.imported",
        summary=f"Imported {len(created)} contacts",
        target_type="contact",
    )
    return ImportOut(created=len(created), contacts=[dump(c) for c in created])


@router.post("/sample", response_model=ImportOut)
async def sample(body: SampleRequest, ctx: ContextDep, session: SessionDep) -> ImportOut:
    ws = require_workspace(ctx)
    created = await generate_sample(session, workspace_id=ws, count=body.count)
    return ImportOut(created=len(created), contacts=[dump(c) for c in created])


@router.get("", response_model=list[ContactOut])
async def list_contacts_endpoint(
    ctx: ContextDep,
    session: SessionDep,
    q: str | None = None,
    source: str | None = None,
    limit: Annotated[int, Query(le=500)] = 200,
    offset: int = 0,
) -> list[ContactOut]:
    ws = require_workspace(ctx)
    stmt = select(Contact).where(Contact.workspace_id == ws)
    if q:
        like = f"%{q.lower()}%"
        stmt = stmt.where(
            or_(
                Contact.full_name.ilike(like),
                Contact.company.ilike(like),
                Contact.title.ilike(like),
            )
        )
    if source:
        stmt = stmt.where(Contact.source == source)
    stmt = stmt.order_by(Contact.full_name).limit(limit).offset(offset)
    return [dump(c) for c in (await session.execute(stmt)).scalars().all()]


async def _owned_contact(session: SessionDep, ws: str, contact_id: str) -> Contact:
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.workspace_id != ws:
        raise HTTPException(status_code=404, detail="contact not found")
    return contact


@router.patch("/{contact_id}", response_model=ContactOut)
async def update_contact(
    contact_id: str, body: ContactPatch, ctx: ContextDep, session: SessionDep
) -> ContactOut:
    ws = require_workspace(ctx)
    contact = await _owned_contact(session, ws, contact_id)
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(contact, field, value)
    await session.flush()
    return dump(contact)


@router.delete("/{contact_id}", response_model=DeleteOut)
async def delete_contact(contact_id: str, ctx: ContextDep, session: SessionDep) -> DeleteOut:
    ws = require_workspace(ctx)
    contact = await _owned_contact(session, ws, contact_id)
    await session.delete(contact)
    await session.flush()
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="contact.deleted",
        summary="Deleted a contact",
        target_type="contact",
        target_id=contact_id,
    )
    return DeleteOut(status="deleted", id=contact_id)


@router.post("/{contact_id}/forget", response_model=DeleteOut)
async def forget_contact(contact_id: str, ctx: ContextDep, session: SessionDep) -> DeleteOut:
    """GDPR erasure: delete the contact and suppress their email so they're never re-imported."""
    ws = require_workspace(ctx)
    contact = await _owned_contact(session, ws, contact_id)
    if contact.email:
        await suppression.suppress(
            session,
            organization_id=ctx.org_id,
            email=contact.email,
            reason=SuppressionReason.manual,
            note="erased (GDPR)",
        )
    await session.delete(contact)
    await session.flush()
    await audit.record(
        session,
        org_id=ctx.org_id,
        workspace_id=ctx.current_workspace_id,
        actor_user_id=ctx.user_id,
        action="contact.forgotten",
        summary="Erased a contact (GDPR)",
        target_type="contact",
        target_id=contact_id,
    )
    return DeleteOut(status="forgotten", id=contact_id)


@router.get("/{contact_id}", response_model=ContactDetailOut)
async def get_contact(contact_id: str, ctx: ContextDep, session: SessionDep) -> ContactDetailOut:
    ws = require_workspace(ctx)
    contact = await session.get(Contact, contact_id)
    if contact is None or contact.workspace_id != ws:
        raise HTTPException(status_code=404, detail="contact not found")
    rows = (
        (
            await session.execute(
                select(Enrollment, Campaign)
                .join(Campaign, Enrollment.campaign_id == Campaign.id)
                .where(Enrollment.contact_id == contact_id)
                .order_by(Enrollment.created_at.desc())
            )
        )
        .tuples()
        .all()
    )
    enrollments = [
        ContactEnrollmentOut(
            id=e.id,
            campaign_id=e.campaign_id,
            campaign_name=c.name,
            state=e.state.value,
            score=e.score,
            current_step=e.current_step,
        )
        for e, c in rows
    ]

    # Activity timeline: every real message (sent + received) plus queued (scheduled) next-sends.
    msg_rows = (
        (
            await session.execute(
                select(Message, Campaign)
                .join(Enrollment, Message.enrollment_id == Enrollment.id)
                .join(Campaign, Enrollment.campaign_id == Campaign.id)
                .where(
                    Enrollment.contact_id == contact_id,
                    (Message.status != MessageStatus.draft) | (Message.scheduled_at.isnot(None)),
                )
                .order_by(Message.created_at.desc())
                .limit(40)
            )
        )
        .tuples()
        .all()
    )
    activity = [
        ContactActivityOut(
            id=m.id,
            direction=m.direction.value,
            channel=m.channel.value,
            status=m.status.value,
            subject=m.subject,
            body=m.body,
            created_at=m.created_at.isoformat() if m.created_at else None,
            scheduled_at=m.scheduled_at.isoformat() if m.scheduled_at else None,
            campaign_name=camp.name,
        )
        for m, camp in msg_rows
    ]

    sent_recv = [m for m, _ in msg_rows if m.status != MessageStatus.draft]
    stats = ContactStatsOut(
        best_score=max((e.score for e, _ in rows), default=0),
        campaigns=len(enrollments),
        replies=sum(1 for m in sent_recv if m.direction == MessageDirection.inbound),
        last_activity_at=(
            sent_recv[0].created_at.isoformat() if sent_recv and sent_recv[0].created_at else None
        ),
    )
    return ContactDetailOut(
        **dump(contact).model_dump(),
        enrollments=enrollments,
        activity=activity,
        stats=stats,
    )
