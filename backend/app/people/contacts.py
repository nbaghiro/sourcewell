"""Contacts: import / sample generator / list-and-detail (service + endpoints)."""

from typing import Annotated

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, ConfigDict
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonList
from app.deps import ContextDep, SessionDep, require_workspace
from app.insights import audit
from app.models import (
    Campaign,
    Contact,
    Enrollment,
    Message,
    MessageDirection,
    MessageStatus,
    SuppressionReason,
)
from app.people import suppression

router = APIRouter(prefix="/contacts", tags=["contacts"])

_SAMPLE: JsonList = [
    {
        "full_name": "Jane Doe",
        "title": "Senior Backend Engineer",
        "company": "Acme",
        "email": "jane@example.com",
        "skills": ["python", "postgres", "distributed systems"],
    },
    {
        "full_name": "Raj Patel",
        "title": "Staff Software Engineer",
        "company": "Globex",
        "email": "raj@example.com",
        "skills": ["python", "kafka", "fintech"],
    },
    {
        "full_name": "Mia Chen",
        "title": "Frontend Engineer",
        "company": "Initech",
        "email": "mia@example.com",
        "skills": ["react", "typescript", "css"],
    },
    {
        "full_name": "Tom Becker",
        "title": "Platform Engineer",
        "company": "Umbrella",
        "email": "tom@example.com",
        "skills": ["kubernetes", "go", "python"],
    },
    {
        "full_name": "Lena Park",
        "title": "Data Engineer",
        "company": "Hooli",
        "email": "lena@example.com",
        "skills": ["python", "spark", "postgres"],
    },
]


# --- Service -----------------------------------------------------------------


async def create_contacts(
    session: AsyncSession,
    *,
    workspace_id: str,
    items: JsonList,
    source: str = "manual",
) -> list[Contact]:
    created: list[Contact] = []
    for it in items:
        contact = Contact(
            workspace_id=workspace_id,
            full_name=it["full_name"],
            title=it.get("title"),
            company=it.get("company"),
            location=it.get("location"),
            email=it.get("email"),
            linkedin_url=it.get("linkedin_url"),
            avatar_url=it.get("avatar_url"),
            skills=it.get("skills") or [],
            source=it.get("source") or source,
            notes=it.get("notes"),
            tags=it.get("tags") or [],
            company_size=it.get("company_size"),
            industry=it.get("industry"),
        )
        session.add(contact)
        created.append(contact)
    await session.flush()
    return created


async def generate_sample(session: AsyncSession, *, workspace_id: str, count: int) -> list[Contact]:
    items = [_SAMPLE[i % len(_SAMPLE)] for i in range(count)]
    return await create_contacts(session, workspace_id=workspace_id, items=items, source="sample")


async def list_contacts(session: AsyncSession, *, workspace_id: str) -> list[Contact]:
    rows = await session.execute(
        select(Contact).where(Contact.workspace_id == workspace_id).order_by(Contact.created_at)
    )
    return list(rows.scalars().all())


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
        ctx,
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
        ctx,
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
        ctx,
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
