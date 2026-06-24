"""Global workspace search across contacts, campaigns, and conversations (business logic)."""

from dataclasses import dataclass, field

from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Campaign, Contact, Enrollment, Message


@dataclass(frozen=True)
class WorkspaceSearchResult:
    contacts: list[Contact] = field(default_factory=list)
    campaigns: list[Campaign] = field(default_factory=list)
    conversations: list[tuple[Enrollment, Contact]] = field(default_factory=list)


async def search_workspace(
    session: AsyncSession, *, workspace_id: str, q: str
) -> WorkspaceSearchResult:
    """Find the top contacts, campaigns, and conversations matching ``q`` in a workspace."""
    term = q.strip()
    if not term:
        return WorkspaceSearchResult()
    like = f"%{term.lower()}%"

    contact_rows = (
        (
            await session.execute(
                select(Contact)
                .where(
                    Contact.workspace_id == workspace_id,
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
                .where(Campaign.workspace_id == workspace_id, func.lower(Campaign.name).like(like))
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
                    Enrollment.workspace_id == workspace_id,
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

    return WorkspaceSearchResult(
        contacts=list(contact_rows),
        campaigns=list(campaign_rows),
        conversations=list(convo_rows),
    )
