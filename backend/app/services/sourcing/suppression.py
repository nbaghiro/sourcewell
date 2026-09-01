"""Suppression list (org do-not-contact): logic and signed unsubscribe tokens."""

import time

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.crypto import sign, verify
from app.models import Suppression, SuppressionReason


def normalize(email: str | None) -> str:
    return (email or "").strip().lower()


async def is_suppressed(
    session: AsyncSession, *, organization_id: str, email: str | None, workspace_id: str | None
) -> bool:
    """True when an org-wide entry, or one scoped to `workspace_id`, covers this address."""
    e = normalize(email)
    if not e:
        return False
    row = (
        (
            await session.execute(
                select(Suppression.id)
                .where(
                    Suppression.organization_id == organization_id,
                    Suppression.email == e,
                    or_(
                        Suppression.workspace_id.is_(None),
                        Suppression.workspace_id == workspace_id,
                    ),
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    return row is not None


async def suppress(
    session: AsyncSession,
    *,
    organization_id: str,
    email: str | None,
    reason: SuppressionReason = SuppressionReason.manual,
    contact_id: str | None = None,
    note: str | None = None,
    workspace_id: str | None = None,
) -> Suppression | None:
    """Add an email to the do-not-contact list (idempotent).

    Org-wide unless a workspace is named.
    """
    e = normalize(email)
    if not e:
        return None
    existing = (
        (
            await session.execute(
                select(Suppression)
                .where(
                    Suppression.organization_id == organization_id,
                    Suppression.email == e,
                    Suppression.workspace_id.is_(None)
                    if workspace_id is None
                    else Suppression.workspace_id == workspace_id,
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return existing
    row = Suppression(
        organization_id=organization_id,
        workspace_id=workspace_id,
        email=e,
        reason=reason,
        contact_id=contact_id,
        note=note,
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
    """Un-suppress an address everywhere in the org (org-wide row and any workspace rows)."""
    rows = list(
        (
            await session.execute(
                select(Suppression).where(
                    Suppression.organization_id == organization_id,
                    Suppression.email == normalize(email),
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        await session.delete(row)
    await session.flush()
    return bool(rows)


# How long an unsubscribe link stays valid. Deliberately long: people unsubscribe from mail they
# received months ago, and a link that has quietly stopped working is worse than no link at all —
# it strands someone who is actively trying to opt out. The bound exists so a leaked token isn't
# good forever, and so "invalid or expired" is a true statement rather than a guess.
UNSUBSCRIBE_TTL_SECONDS = 365 * 24 * 60 * 60


def unsubscribe_token(organization_id: str, email: str, *, issued_at: float | None = None) -> str:
    stamp = int(issued_at if issued_at is not None else time.time())
    return sign(f"{organization_id}|{normalize(email)}|{stamp}")


def unsubscribe_url(organization_id: str, email: str) -> str:
    base = get_settings().api_base_url.rstrip("/")
    return f"{base}/unsubscribe?token={unsubscribe_token(organization_id, email)}"


def parse_unsubscribe(token: str, *, now: float | None = None) -> tuple[str, str] | None:
    """`(organization_id, email)` from a signed opt-out token, or None if it doesn't hold up.

    Tokens minted before the issue time was added carry two fields instead of three. Those are
    already sitting in real inboxes, so they keep working: breaking a live unsubscribe link to
    tidy up a format is exactly the failure this endpoint exists to prevent.
    """
    payload = verify(token)
    if not payload:
        return None
    parts = payload.split("|")
    if len(parts) == 2:
        return parts[0], parts[1]
    if len(parts) != 3:
        return None
    org_id, email, stamp = parts
    try:
        issued = float(stamp)
    except ValueError:
        return None
    if (now if now is not None else time.time()) - issued > UNSUBSCRIBE_TTL_SECONDS:
        return None
    return org_id, email
