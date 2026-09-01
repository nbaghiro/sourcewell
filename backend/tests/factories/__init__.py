"""Builders that insert tenancy rows directly into a session for tests."""

from datetime import UTC, datetime

from httpx import AsyncClient, Response
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
from app.services.workspace import auth as auth_service


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
    session: AsyncSession,
    *,
    org: Organization | None = None,
    role: MembershipRole = MembershipRole.org_admin,
    name: str = "User",
    email: str | None = None,
    verified: bool = True,
    profile_complete: bool = True,
) -> User:
    """An established account: email-verified and past signup, which is what almost every test
    wants. `verified=False` exercises the email gate; `profile_complete=False` exercises the
    OAuth signup-completion gate. Identity is global, so `org` is optional — passing it also
    writes the membership that puts the user in that organization."""
    user = User(
        email=email or f"{new_id()}@example.com",
        name=name,
        email_verified_at=datetime.now(UTC) if verified else None,
        profile_completed_at=datetime.now(UTC) if profile_complete else None,
    )
    session.add(user)
    await session.flush()
    if org is not None:
        await make_membership(session, user=user, org=org, role=role)
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


async def oauth_callback(client: AsyncClient, *, code: str = "any") -> Response:
    """Drive `/auth/callback` the way a browser that actually started the flow does.

    The endpoint refuses a code that arrives without the `state` nonce `/auth/login/{provider}`
    parked in a cookie — that check is what stops an attacker aiming their own code at someone
    else's browser — so every test that exercises the callback has to carry one.
    """
    state = auth_service.new_oauth_state()
    client.cookies.set(auth_service.OAUTH_STATE_COOKIE, state)
    return await client.get(f"/auth/callback?code={code}&state={state}")
