"""Tenancy: workspace/org bootstrap CRUD (service layer).

Request-context DI lives in `app/deps.py`; HTTP endpoints + schemas live in `app/api/tenancy.py`.
"""

from datetime import UTC, datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Membership,
    MembershipRole,
    Organization,
    SpaceGrant,
    SpaceRole,
    User,
    UserStatus,
    Workspace,
    WorkspaceKind,
)


async def signup(
    session: AsyncSession,
    *,
    org_name: str,
    slug: str,
    admin_email: str,
    admin_name: str,
    first_name: str | None = None,
    last_name: str | None = None,
    username: str | None = None,
    avatar_url: str | None = None,
    password_hash: str | None = None,
) -> tuple[Organization, User]:
    """Bootstrap: create an organization (with its default workspace) + its first admin.

    The profile fields (first/last name, username, avatar, password) come from the self-serve
    signup form; API-driven bootstraps pass only the org + admin identity.
    """
    org = await create_organization(session, name=org_name, slug=slug)

    user = User(
        email=admin_email,
        name=admin_name,
        first_name=first_name,
        last_name=last_name,
        username=username,
        avatar_url=avatar_url,
        password_hash=password_hash,
        # Whoever creates an org this way has already supplied everything there is to ask for —
        # the signup form, or a direct API bootstrap. Only a first OAuth sign-in owes a profile,
        # and that path goes through `connections.provision_user` instead.
        profile_completed_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()

    membership = Membership(user_id=user.id, organization_id=org.id, role=MembershipRole.org_admin)
    session.add(membership)
    await session.flush()
    return org, user


# Every tenant needs somewhere to work: `require_workspace` rejects a request with no workspace,
# so an organization without one is an account that can't do anything. Both signup doors and the
# API bootstrap go through here, so the invariant holds however an org comes into being.
DEFAULT_WORKSPACE_NAME = "Default workspace"


async def create_organization(session: AsyncSession, *, name: str, slug: str) -> Organization:
    """A new organization and the default workspace that makes it usable."""
    org = Organization(name=name, slug=slug)
    session.add(org)
    await session.flush()
    session.add(
        Workspace(organization_id=org.id, name=DEFAULT_WORKSPACE_NAME, kind=WorkspaceKind.team)
    )
    await session.flush()
    return org


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


async def create_user(session: AsyncSession, *, email: str, name: str) -> User:
    # An invited teammate joins an org that already exists: no company to name, no password to
    # set. Nothing is owed, so they are never sent to the completion form.
    user = User(
        email=email,
        name=name,
        status=UserStatus.invited,
        profile_completed_at=datetime.now(UTC),
    )
    session.add(user)
    await session.flush()
    return user


async def add_membership(
    session: AsyncSession, *, org_id: str, user_id: str, role: MembershipRole
) -> Membership:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")
    membership = Membership(user_id=user_id, organization_id=org_id, role=role)
    session.add(membership)
    await session.flush()
    return membership


async def add_space_grant(
    session: AsyncSession, *, org_id: str, user_id: str, workspace_id: str, role: SpaceRole
) -> SpaceGrant:
    member = (
        await session.execute(
            select(Membership).where(
                Membership.user_id == user_id, Membership.organization_id == org_id
            )
        )
    ).scalar_one_or_none()
    if member is None:
        raise HTTPException(status_code=404, detail="user is not a member of this organization")
    ws = await session.get(Workspace, workspace_id)
    if ws is None or ws.organization_id != org_id:
        raise HTTPException(status_code=404, detail="workspace not found")
    grant = SpaceGrant(user_id=user_id, workspace_id=workspace_id, role=role)
    session.add(grant)
    await session.flush()
    return grant
