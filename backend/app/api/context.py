"""Request-scoped dependency injection — the tenant context every router needs.

Identity resolution (cookie/header → user) is delegated to `services/workspace/auth`; this module
computes the *tenant access* (org/workspace membership, `X-Workspace-Id` scope) and exposes the
FastAPI deps + guards.

Lives in the api layer: `api/` routers import the FastAPI deps (`ContextDep`/`SessionDep`), and
guards import `TenantContext`.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import Membership, MembershipRole, SpaceGrant, User, Workspace
from app.services.workspace import auth

SessionDep = Annotated[AsyncSession, Depends(get_session)]

# Org roles that reach every workspace in their organization without an explicit grant.
_ORG_WIDE_ROLES = {MembershipRole.org_admin, MembershipRole.compliance}


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
    if not memberships:
        raise HTTPException(status_code=403, detail="no organization membership")

    org_ids = {m.organization_id for m in memberships}
    org_wide = {m.organization_id for m in memberships if m.role in _ORG_WIDE_ROLES}
    granted = await _granted_workspace_ids(session, user_id=user_id)
    reachable = (
        (
            await session.execute(
                select(Workspace.id, Workspace.organization_id).where(
                    or_(
                        Workspace.organization_id.in_(org_wide),
                        Workspace.id.in_(granted),
                    )
                )
            )
        )
        .tuples()
        .all()
    )
    workspace_org = dict(reachable)
    ws_ids = frozenset(workspace_org)

    current = request.headers.get("X-Workspace-Id")
    if current is not None and current not in ws_ids:
        raise HTTPException(status_code=403, detail="workspace not accessible")

    if current is not None:
        org_id = workspace_org[current]
    elif len(org_ids) == 1:
        org_id = next(iter(org_ids))
    else:
        header_org = request.headers.get("X-Organization-Id")
        if header_org is not None and header_org not in org_ids:
            raise HTTPException(status_code=403, detail="organization not accessible")
        # A fresh browser sends no selection; fall back to the oldest membership so a multi-org
        # user is never locked out of the app before they can choose. Rows written in one
        # transaction share a timestamp, so the id breaks the tie.
        oldest = min(memberships, key=lambda m: (m.created_at, m.id))
        org_id = header_org or oldest.organization_id

    roles = frozenset(m.role for m in memberships if m.organization_id == org_id)
    return TenantContext(
        user_id=user_id,
        org_id=org_id,
        roles=roles,
        is_org_admin=MembershipRole.org_admin in roles,
        allowed_workspace_ids=ws_ids,
        current_workspace_id=current,
    )


async def _granted_workspace_ids(session: AsyncSession, *, user_id: str) -> frozenset[str]:
    """Workspaces reached by an explicit `SpaceGrant`."""
    return frozenset(
        (
            await session.execute(
                select(SpaceGrant.workspace_id).where(SpaceGrant.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )


ContextDep = Annotated[TenantContext, Depends(get_context)]
