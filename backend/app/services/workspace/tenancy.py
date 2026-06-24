"""Tenancy: workspace/org bootstrap CRUD (service layer).

Request-context DI lives in `app/deps.py`; HTTP endpoints + schemas live in `app/api/tenancy.py`.
"""

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Membership,
    MembershipRole,
    MembershipScope,
    Organization,
    User,
    UserStatus,
    Workspace,
    WorkspaceKind,
)


async def signup(
    session: AsyncSession, *, org_name: str, slug: str, admin_email: str, admin_name: str
) -> tuple[Organization, User]:
    """Bootstrap: create an organization + its first admin user + org_admin membership."""
    org = Organization(name=org_name, slug=slug)
    session.add(org)
    await session.flush()

    user = User(organization_id=org.id, email=admin_email, name=admin_name)
    session.add(user)
    await session.flush()

    membership = Membership(
        user_id=user.id,
        organization_id=org.id,
        scope=MembershipScope.organization,
        role=MembershipRole.org_admin,
    )
    session.add(membership)
    await session.flush()
    return org, user


async def create_workspace(
    session: AsyncSession, *, org_id: str, name: str, kind: WorkspaceKind
) -> Workspace:
    ws = Workspace(organization_id=org_id, name=name, kind=kind)
    session.add(ws)
    await session.flush()
    return ws


async def list_workspaces(
    session: AsyncSession, *, org_id: str, allowed_ids: frozenset[str]
) -> list[Workspace]:
    stmt = (
        select(Workspace)
        .where(Workspace.organization_id == org_id, Workspace.id.in_(allowed_ids))
        .order_by(Workspace.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def get_workspace(
    session: AsyncSession, *, org_id: str, allowed_ids: frozenset[str], workspace_id: str
) -> Workspace:
    ws = await session.get(Workspace, workspace_id)
    if ws is None or ws.organization_id != org_id or ws.id not in allowed_ids:
        raise HTTPException(status_code=404, detail="workspace not found")
    return ws


async def create_user(session: AsyncSession, *, org_id: str, email: str, name: str) -> User:
    user = User(organization_id=org_id, email=email, name=name, status=UserStatus.invited)
    session.add(user)
    await session.flush()
    return user


async def add_membership(
    session: AsyncSession,
    *,
    org_id: str,
    user_id: str,
    scope: MembershipScope,
    role: MembershipRole,
    workspace_id: str | None,
) -> Membership:
    user = await session.get(User, user_id)
    if user is None or user.organization_id != org_id:
        raise HTTPException(status_code=404, detail="user not found")
    if scope == MembershipScope.workspace:
        if workspace_id is None:
            raise HTTPException(status_code=422, detail="workspace_id required for workspace scope")
        ws = await session.get(Workspace, workspace_id)
        if ws is None or ws.organization_id != org_id:
            raise HTTPException(status_code=404, detail="workspace not found")
    else:
        workspace_id = None

    membership = Membership(
        user_id=user_id,
        organization_id=org_id,
        scope=scope,
        role=role,
        workspace_id=workspace_id,
    )
    session.add(membership)
    await session.flush()
    return membership
