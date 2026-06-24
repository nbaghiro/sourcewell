"""Audit trail: the write helper shared by every mutating api handler."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent


async def record(
    session: AsyncSession,
    *,
    org_id: str,
    workspace_id: str | None,
    actor_user_id: str,
    action: str,
    summary: str,
    target_type: str | None = None,
    target_id: str | None = None,
) -> None:
    """Append an audit event for the current actor + org/workspace."""
    session.add(
        AuditEvent(
            organization_id=org_id,
            workspace_id=workspace_id,
            actor_user_id=actor_user_id,
            action=action,
            target_type=target_type,
            target_id=target_id,
            summary=summary,
        )
    )
    await session.flush()
