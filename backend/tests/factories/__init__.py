"""Builders that insert tenancy rows directly into a session for tests."""

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import new_id
from app.models import (
    Membership,
    MembershipRole,
    Organization,
    SpaceGrant,
    SpaceRole,
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


async def make_user(session: AsyncSession, *, name: str = "User", email: str | None = None) -> User:
    user = User(email=email or f"{new_id()}@example.com", name=name)
    session.add(user)
    await session.flush()
    return user


async def make_membership(
    session: AsyncSession,
    *,
    user: User,
    org: Organization,
    role: MembershipRole = MembershipRole.member,
) -> Membership:
    membership = Membership(user_id=user.id, organization_id=org.id, role=role)
    session.add(membership)
    await session.flush()
    return membership


async def make_space_grant(
    session: AsyncSession, *, user: User, workspace: Workspace, role: SpaceRole = SpaceRole.member
) -> SpaceGrant:
    grant = SpaceGrant(user_id=user.id, workspace_id=workspace.id, role=role)
    session.add(grant)
    await session.flush()
    return grant


async def make_org_admin(session: AsyncSession, *, org: Organization, name: str = "Admin") -> User:
    user = await make_user(session, name=name)
    await make_membership(session, user=user, org=org, role=MembershipRole.org_admin)
    return user


async def make_workspace_member(
    session: AsyncSession, *, org: Organization, workspace: Workspace, name: str = "Member"
) -> User:
    """A plain org member whose only workspace access is an explicit grant."""
    user = await make_user(session, name=name)
    await make_membership(session, user=user, org=org, role=MembershipRole.member)
    await make_space_grant(session, user=user, workspace=workspace)
    return user
