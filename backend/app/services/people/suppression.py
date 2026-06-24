"""Suppression list (org do-not-contact): logic and signed unsubscribe tokens."""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.signing import sign, verify
from app.models import Suppression, SuppressionReason


def normalize(email: str | None) -> str:
    return (email or "").strip().lower()


async def is_suppressed(session: AsyncSession, *, organization_id: str, email: str | None) -> bool:
    e = normalize(email)
    if not e:
        return False
    row = (
        await session.execute(
            select(Suppression).where(
                Suppression.organization_id == organization_id, Suppression.email == e
            )
        )
    ).scalar_one_or_none()
    return row is not None


async def suppress(
    session: AsyncSession,
    *,
    organization_id: str,
    email: str | None,
    reason: SuppressionReason = SuppressionReason.manual,
    contact_id: str | None = None,
    note: str | None = None,
) -> Suppression | None:
    """Add an email to the org's do-not-contact list (idempotent)."""
    e = normalize(email)
    if not e:
        return None
    existing = (
        await session.execute(
            select(Suppression).where(
                Suppression.organization_id == organization_id, Suppression.email == e
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = Suppression(
        organization_id=organization_id, email=e, reason=reason, contact_id=contact_id, note=note
    )
    session.add(row)
    await session.flush()
    return row


async def list_for_org(session: AsyncSession, organization_id: str) -> list[Suppression]:
    rows = await session.execute(
        select(Suppression)
        .where(Suppression.organization_id == organization_id)
        .order_by(Suppression.created_at.desc())
    )
    return list(rows.scalars().all())


async def remove(session: AsyncSession, *, organization_id: str, email: str) -> bool:
    row = (
        await session.execute(
            select(Suppression).where(
                Suppression.organization_id == organization_id,
                Suppression.email == normalize(email),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    await session.delete(row)
    await session.flush()
    return True


def unsubscribe_token(organization_id: str, email: str) -> str:
    return sign(f"{organization_id}|{normalize(email)}")


def unsubscribe_url(organization_id: str, email: str) -> str:
    base = get_settings().api_base_url.rstrip("/")
    return f"{base}/unsubscribe?token={unsubscribe_token(organization_id, email)}"


def parse_unsubscribe(token: str) -> tuple[str, str] | None:
    payload = verify(token)
    if not payload or "|" not in payload:
        return None
    org_id, email = payload.split("|", 1)
    return org_id, email
