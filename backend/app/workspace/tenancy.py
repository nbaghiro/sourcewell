"""Tenancy: workspace/org bootstrap CRUD + endpoints. Request-context DI lives in `app/deps.py`."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.deps import ContextDep, SessionDep, require_org_admin
from app.models import (
    Membership,
    MembershipRole,
    MembershipScope,
    Organization,
    User,
    UserStatus,
    Workspace,
    WorkspaceKind,
    WorkspaceStatus,
)

# --- Schemas -----------------------------------------------------------------


class SignupRequest(BaseModel):
    org_name: str
    slug: str
    admin_email: str
    admin_name: str


class SignupResponse(BaseModel):
    organization_id: str
    admin_user_id: str


class WorkspaceCreate(BaseModel):
    name: str
    kind: WorkspaceKind = WorkspaceKind.client


class WorkspaceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    organization_id: str
    name: str
    kind: WorkspaceKind
    status: WorkspaceStatus


class UserCreate(BaseModel):
    email: str
    name: str


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    name: str
    status: UserStatus


class MembershipCreate(BaseModel):
    user_id: str
    scope: MembershipScope
    role: MembershipRole
    workspace_id: str | None = None


class MembershipRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    user_id: str
    scope: MembershipScope
    role: MembershipRole
    workspace_id: str | None


class MeResponse(BaseModel):
    user_id: str
    org_id: str
    roles: list[MembershipRole]
    is_org_admin: bool
    allowed_workspace_ids: list[str]
    current_workspace_id: str | None


# --- Service -----------------------------------------------------------------


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


# --- Router ------------------------------------------------------------------

router = APIRouter(tags=["tenancy"])


@router.post("/organizations", response_model=SignupResponse, status_code=201)
async def signup_endpoint(body: SignupRequest, session: SessionDep) -> SignupResponse:
    org, user = await signup(
        session,
        org_name=body.org_name,
        slug=body.slug,
        admin_email=body.admin_email,
        admin_name=body.admin_name,
    )
    return SignupResponse(organization_id=org.id, admin_user_id=user.id)


@router.get("/me", response_model=MeResponse)
async def me(ctx: ContextDep) -> MeResponse:
    return MeResponse(
        user_id=ctx.user_id,
        org_id=ctx.org_id,
        roles=sorted(ctx.roles),
        is_org_admin=ctx.is_org_admin,
        allowed_workspace_ids=sorted(ctx.allowed_workspace_ids),
        current_workspace_id=ctx.current_workspace_id,
    )


@router.post("/workspaces", response_model=WorkspaceRead, status_code=201)
async def create_workspace_endpoint(
    body: WorkspaceCreate, ctx: ContextDep, session: SessionDep
) -> Workspace:
    require_org_admin(ctx)
    return await create_workspace(session, org_id=ctx.org_id, name=body.name, kind=body.kind)


@router.get("/workspaces", response_model=list[WorkspaceRead])
async def list_workspaces_endpoint(ctx: ContextDep, session: SessionDep) -> list[Workspace]:
    return await list_workspaces(session, org_id=ctx.org_id, allowed_ids=ctx.allowed_workspace_ids)


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceRead)
async def get_workspace_endpoint(
    workspace_id: str, ctx: ContextDep, session: SessionDep
) -> Workspace:
    return await get_workspace(
        session,
        org_id=ctx.org_id,
        allowed_ids=ctx.allowed_workspace_ids,
        workspace_id=workspace_id,
    )


@router.post("/users", response_model=UserRead, status_code=201)
async def create_user_endpoint(body: UserCreate, ctx: ContextDep, session: SessionDep) -> User:
    require_org_admin(ctx)
    return await create_user(session, org_id=ctx.org_id, email=body.email, name=body.name)


@router.post("/memberships", response_model=MembershipRead, status_code=201)
async def create_membership(
    body: MembershipCreate, ctx: ContextDep, session: SessionDep
) -> Membership:
    require_org_admin(ctx)
    return await add_membership(
        session,
        org_id=ctx.org_id,
        user_id=body.user_id,
        scope=body.scope,
        role=body.role,
        workspace_id=body.workspace_id,
    )
