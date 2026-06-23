"""Builders that insert tenancy rows directly into a session for tests."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import new_id
from app.models import (
    Membership,
    MembershipRole,
    MembershipScope,
    Organization,
    User,
    Workspace,
    WorkspaceKind,
)


async def make_org(session: AsyncSession, *, name: str = "Org", slug: str = "org") -> Organization:
    org = Organization(name=name, slug=slug)
    session.add(org)
    await session.flush()
    return org


async def make_workspace(
    session: AsyncSession,
    *,
    org: Organization,
    name: str = "Workspace",
    kind: WorkspaceKind = WorkspaceKind.client,
) -> Workspace:
    ws = Workspace(organization_id=org.id, name=name, kind=kind)
    session.add(ws)
    await session.flush()
    return ws


async def make_user(
    session: AsyncSession, *, org: Organization, name: str = "User", email: str | None = None
) -> User:
    user = User(organization_id=org.id, email=email or f"{new_id()}@example.com", name=name)
    session.add(user)
    await session.flush()
    return user


async def make_membership(
    session: AsyncSession,
    *,
    user: User,
    org: Organization,
    scope: MembershipScope,
    role: MembershipRole,
    workspace: Workspace | None = None,
) -> Membership:
    membership = Membership(
        user_id=user.id,
        organization_id=org.id,
        scope=scope,
        role=role,
        workspace_id=workspace.id if workspace else None,
    )
    session.add(membership)
    await session.flush()
    return membership


async def make_org_admin(session: AsyncSession, *, org: Organization, name: str = "Admin") -> User:
    user = await make_user(session, org=org, name=name)
    await make_membership(
        session,
        user=user,
        org=org,
        scope=MembershipScope.organization,
        role=MembershipRole.org_admin,
    )
    return user
