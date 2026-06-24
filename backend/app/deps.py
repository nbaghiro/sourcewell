"""Request-scoped dependency injection — the tenant context every router needs.

Identity resolution (cookie/header → user) is delegated to `services/workspace/auth`; this module
computes the *tenant access* (org/workspace membership, `X-Workspace-Id` scope) and exposes the
FastAPI deps + guards.

Shared kernel (root, peer to `models.py`/`targeting.py`): `api/` routers import the FastAPI deps
(`ContextDep`/`SessionDep`), and services that operate on a request import `TenantContext`.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import Membership, MembershipRole, MembershipScope, User, Workspace
from app.services.workspace import auth

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class TenantContext:
    user_id: str
    org_id: str
    roles: frozenset[MembershipRole]
    is_org_admin: bool
    allowed_workspace_ids: frozenset[str]
    current_workspace_id: str | None


async def get_context(request: Request, response: Response, session: SessionDep) -> TenantContext:
    user_id = await auth.resolve_user_from_request(request, response, session)
    if user_id is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="unknown user")

    memberships = list(
        (await session.execute(select(Membership).where(Membership.user_id == user_id)))
        .scalars()
        .all()
    )
    roles = frozenset(m.role for m in memberships)
    is_org_admin = any(
        m.scope == MembershipScope.organization and m.role == MembershipRole.org_admin
        for m in memberships
    )
    # Org-scoped members (org_admin / compliance) see every workspace in the org;
    # workspace-scoped members see only their assigned workspaces.
    org_scoped = any(m.scope == MembershipScope.organization for m in memberships)
    if org_scoped:
        ws_ids = frozenset(
            (
                await session.execute(
                    select(Workspace.id).where(Workspace.organization_id == user.organization_id)
                )
            )
            .scalars()
            .all()
        )
    else:
        ws_ids = frozenset(m.workspace_id for m in memberships if m.workspace_id is not None)

    current = request.headers.get("X-Workspace-Id")
    if current is not None and current not in ws_ids:
        raise HTTPException(status_code=403, detail="workspace not accessible")

    return TenantContext(
        user_id=user_id,
        org_id=user.organization_id,
        roles=roles,
        is_org_admin=is_org_admin,
        allowed_workspace_ids=ws_ids,
        current_workspace_id=current,
    )


ContextDep = Annotated[TenantContext, Depends(get_context)]


def require_org_admin(ctx: TenantContext) -> None:
    if not ctx.is_org_admin:
        raise HTTPException(status_code=403, detail="organization admin required")


def require_workspace(ctx: TenantContext) -> str:
    """Resolve the workspace the request operates on (X-Workspace-Id, already access-checked)."""
    if ctx.current_workspace_id is None:
        raise HTTPException(status_code=400, detail="missing X-Workspace-Id")
    return ctx.current_workspace_id
