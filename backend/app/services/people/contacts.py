"""Contacts: import / sample generator / list (business logic)."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.types import JsonList
from app.models import Contact

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
