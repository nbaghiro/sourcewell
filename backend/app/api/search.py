"""Global workspace search HTTP layer (for the ⌘K palette): endpoint and response schemas."""

from fastapi import APIRouter
from pydantic import BaseModel

from app.deps import ContextDep, SessionDep, require_workspace
from app.services.insights.search import search_workspace

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
    result = await search_workspace(session, workspace_id=ws, q=q)
    return SearchOut(
        contacts=[
            SearchContact(id=c.id, full_name=c.full_name, title=c.title, avatar_url=c.avatar_url)
            for c in result.contacts
        ],
        campaigns=[
            SearchCampaign(id=c.id, name=c.name, status=c.status.value) for c in result.campaigns
        ],
        conversations=[
            SearchConversation(
                enrollment_id=e.id,
                contact_name=c.full_name,
                avatar_url=c.avatar_url,
                state=e.state.value,
            )
            for e, c in result.conversations
        ],
    )
