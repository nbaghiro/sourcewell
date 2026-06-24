"""Per-org, per-provider usage metering (search/enrich/verify/import call counts by day)."""

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ProviderUsage


async def record(
    session: AsyncSession, *, organization_id: str, provider: str, kind: str, count: int = 1
) -> None:
    """Increment today's counter for (org, provider, kind). Best-effort."""
    if count <= 0:
        return
    today = datetime.now(UTC).date()
    row = (
        await session.execute(
            select(ProviderUsage).where(
                ProviderUsage.organization_id == organization_id,
                ProviderUsage.provider == provider,
                ProviderUsage.kind == kind,
                ProviderUsage.day == today,
            )
        )
    ).scalar_one_or_none()
    if row is None:
        session.add(
            ProviderUsage(
                organization_id=organization_id,
                provider=provider,
                kind=kind,
                day=today,
                count=count,
            )
        )
    else:
        row.count += count
    await session.flush()


async def summary(session: AsyncSession, organization_id: str) -> list[dict[str, object]]:
    rows = (
        (
            await session.execute(
                select(ProviderUsage)
                .where(ProviderUsage.organization_id == organization_id)
                .order_by(ProviderUsage.day.desc())
            )
        )
        .scalars()
        .all()
    )
    return [
        {"provider": r.provider, "kind": r.kind, "day": r.day.isoformat(), "count": r.count}
        for r in rows
    ]
