"""Request-scoped dependency injection — the tenant context every router needs.

Identity resolution (cookie/header → user) is delegated to `services/workspace/auth`; this module
computes the *tenant access* (org/workspace membership, `X-Workspace-Id` scope) and exposes the
FastAPI deps + guards.

Lives in the api layer: `api/` routers import the FastAPI deps (`ContextDep`/`SessionDep`), and
guards import `TenantContext`.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_session
from app.models import ORG_WIDE_ROLES, Membership, MembershipRole, SpaceGrant, User, Workspace
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
    # False while an OAuth signup is unfinished. Only the two endpoints that exist to finish it
    # (`SignupContextDep`) ever see a context with this false — `get_context` refuses the rest.
    profile_complete: bool


async def get_signup_context(request: Request, session: SessionDep) -> TenantContext:
    """The tenant context *without* the signup-complete gate.

    Only for the two endpoints that have to work while a signup is still unfinished: `GET
    /auth/me` (which is how the client learns it is unfinished) and `POST /auth/complete-profile`
    (which finishes it). Everything else takes `ContextDep`.
    """
    user_id = await auth.resolve_user_from_request(request, session)
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
    org_wide = {m.organization_id for m in memberships if m.role in ORG_WIDE_ROLES}
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
        profile_complete=user.profile_completed_at is not None,
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


async def get_context(ctx: Annotated[TenantContext, Depends(get_signup_context)]) -> TenantContext:
    """The tenant context every ordinary endpoint takes — and the signup-complete gate.

    A first Google/Microsoft sign-in provisions the account and mints a session *before* asking
    for the username, company and avatar the provider can't supply. The client routes that user to
    the form, but routing is not enforcement: without this, skipping the form and calling the API
    directly left them working in an org still carrying its placeholder name, under a user with no
    username. 403 rather than 401 — they are signed in, they just aren't finished.
    """
    if not ctx.profile_complete:
        raise HTTPException(status_code=403, detail="profile_incomplete")
    return ctx


ContextDep = Annotated[TenantContext, Depends(get_context)]
SignupContextDep = Annotated[TenantContext, Depends(get_signup_context)]
