"""Request-scoped dependency injection — the tenant context every router needs.

Identity is resolved from the WorkOS sealed-session cookie when present; otherwise it falls back to
the `X-User-Id` dev header (local / API QA). Workspace scope comes from `X-Workspace-Id`.

This is shared kernel (root, peer to `models.py`/`targeting.py`): `api/` routers import the FastAPI
deps (`ContextDep`/`SessionDep`), and services that operate on a request import `TenantContext`.
"""

from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import get_session
from app.models import Membership, MembershipRole, MembershipScope, User, Workspace

SessionDep = Annotated[AsyncSession, Depends(get_session)]


@dataclass(frozen=True)
class TenantContext:
    user_id: str
    org_id: str
    roles: frozenset[MembershipRole]
    is_org_admin: bool
    allowed_workspace_ids: frozenset[str]
    current_workspace_id: str | None


async def _resolve_user_id(
    request: Request, response: Response, session: AsyncSession
) -> str | None:
    """WorkOS session cookie first; then the X-User-Id dev header."""
    # Imported lazily to avoid a circular import (auth imports these deps).
    from app.workspace import auth as auth_service

    settings = get_settings()
    if settings.auth_enabled:
        sealed = request.cookies.get(settings.session_cookie_name)
        if sealed:
            user_id, refreshed = await auth_service.resolve_user_id(session, sealed)
            if refreshed:
                auth_service.set_session_cookie(response, refreshed)
            if user_id:
                return user_id
    elif (dev_id := request.cookies.get(settings.dev_session_cookie_name)) is not None:
        # Dev-login session (only honored when WorkOS is not configured).
        user = await session.get(User, dev_id)
        if user is not None:
            return user.id
    header_id = request.headers.get("X-User-Id")
    if header_id:
        user = await session.get(User, header_id)
        if user is not None:
            return user.id
    return None


async def get_context(request: Request, response: Response, session: SessionDep) -> TenantContext:
    user_id = await _resolve_user_id(request, response, session)
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
