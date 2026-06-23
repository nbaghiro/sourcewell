"""Audit trail: write/read helpers + the org-scoped read endpoint."""

from typing import Annotated

from fastapi import APIRouter, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AuditEvent, User
from app.workspace.tenancy import ContextDep, SessionDep, TenantContext

router = APIRouter(prefix="/audit", tags=["audit"])


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


class AuditEventOut(BaseModel):
    id: str
    action: str
    summary: str
    target_type: str | None
    target_id: str | None
    actor_name: str | None
    workspace_id: str | None
    created_at: str | None


@router.get("", response_model=list[AuditEventOut])
async def audit_log(
    ctx: ContextDep,
    session: SessionDep,
    limit: Annotated[int, Query(le=200)] = 50,
) -> list[AuditEventOut]:
    rows = (
        await session.execute(
            select(AuditEvent, User)
            .outerjoin(User, AuditEvent.actor_user_id == User.id)
            .where(AuditEvent.organization_id == ctx.org_id)
            .order_by(AuditEvent.created_at.desc())
            .limit(limit)
        )
    ).all()
    return [
        AuditEventOut(
            id=e.id,
            action=e.action,
            summary=e.summary,
            target_type=e.target_type,
            target_id=e.target_id,
            actor_name=u.name if u else None,
            workspace_id=e.workspace_id,
            created_at=e.created_at.isoformat() if e.created_at else None,
        )
        for e, u in rows
    ]
