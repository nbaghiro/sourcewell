"""Audit trail: the write helper shared by every mutating api handler."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import TenantContext
from app.models import AuditEvent


async def record(
    session: AsyncSession,
    ctx: TenantContext,
    *,
    action: str,
    summary: str,
    target_type: str | None = None,
    target_id: str | None = None,
) -> None:
    """Append an audit event for the current actor + org/workspace."""
    session.add(
        AuditEvent(
            organization_id=ctx.org_id,
            workspace_id=ctx.current_workspace_id,
            actor_user_id=ctx.user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            summary=summary,
        )
    )
    await session.flush()
