"""Global workspace search across contacts, campaigns, and conversations (for the ⌘K palette)."""

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import exists, func, or_, select

from app.deps import ContextDep, SessionDep, require_workspace
from app.models import Campaign, Contact, Enrollment, Message

router = APIRouter(prefix="/search", tags=["search"])


class SearchContact(BaseModel):
    id: str
    full_name: str
    title: str | None
    avatar_url: str | None


class SearchCampaign(BaseModel):
    id: str
    name: str
    status: str


class SearchConversation(BaseModel):
    enrollment_id: str
    contact_name: str
    avatar_url: str | None
    state: str


class SearchOut(BaseModel):
    contacts: list[SearchContact]
    campaigns: list[SearchCampaign]
    conversations: list[SearchConversation]


@router.get("", response_model=SearchOut)
async def search(q: str, ctx: ContextDep, session: SessionDep) -> SearchOut:
    ws = require_workspace(ctx)
    term = q.strip()
    if not term:
        return SearchOut(contacts=[], campaigns=[], conversations=[])
    like = f"%{term.lower()}%"

    contact_rows = (
        (
            await session.execute(
                select(Contact)
                .where(
                    Contact.workspace_id == ws,
                    or_(
                        func.lower(Contact.full_name).like(like),
                        func.lower(Contact.company).like(like),
                        func.lower(Contact.title).like(like),
                    ),
                )
                .order_by(Contact.full_name)
                .limit(6)
            )
        )
        .scalars()
        .all()
    )
    campaign_rows = (
        (
            await session.execute(
                select(Campaign)
                .where(Campaign.workspace_id == ws, func.lower(Campaign.name).like(like))
                .limit(6)
            )
        )
        .scalars()
        .all()
    )
    convo_rows = (
        (
            await session.execute(
                select(Enrollment, Contact)
                .join(Contact, Enrollment.contact_id == Contact.id)
                .where(
                    Enrollment.workspace_id == ws,
                    func.lower(Contact.full_name).like(like),
                    exists().where(Message.enrollment_id == Enrollment.id),
                )
                .order_by(Enrollment.score.desc())
                .limit(6)
            )
        )
        .tuples()
        .all()
    )

    return SearchOut(
        contacts=[
            SearchContact(id=c.id, full_name=c.full_name, title=c.title, avatar_url=c.avatar_url)
            for c in contact_rows
        ],
        campaigns=[
            SearchCampaign(id=c.id, name=c.name, status=c.status.value) for c in campaign_rows
        ],
        conversations=[
            SearchConversation(
                enrollment_id=e.id,
                contact_name=c.full_name,
                avatar_url=c.avatar_url,
                state=e.state.value,
            )
            for e, c in convo_rows
        ],
    )
