"""Tenancy HTTP endpoints: workspace/org bootstrap CRUD.

Business logic lives in `app.services.workspace.tenancy`; request-context DI lives in `app/deps.py`.
"""

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from app.deps import ContextDep, SessionDep, require_org_admin
from app.models import (
    Membership,
    MembershipRole,
    MembershipScope,
    User,
    UserStatus,
    Workspace,
    WorkspaceKind,
    WorkspaceStatus,
)
from app.services.workspace import tenancy as tenancy_service

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


# --- Router ------------------------------------------------------------------

router = APIRouter(tags=["tenancy"])


@router.post("/organizations", response_model=SignupResponse, status_code=201)
async def signup_endpoint(body: SignupRequest, session: SessionDep) -> SignupResponse:
    org, user = await tenancy_service.signup(
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
    return await tenancy_service.create_workspace(
        session, org_id=ctx.org_id, name=body.name, kind=body.kind
    )


@router.get("/workspaces", response_model=list[WorkspaceRead])
async def list_workspaces_endpoint(ctx: ContextDep, session: SessionDep) -> list[Workspace]:
    return await tenancy_service.list_workspaces(
        session, org_id=ctx.org_id, allowed_ids=ctx.allowed_workspace_ids
    )


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceRead)
async def get_workspace_endpoint(
    workspace_id: str, ctx: ContextDep, session: SessionDep
) -> Workspace:
    return await tenancy_service.get_workspace(
        session,
        org_id=ctx.org_id,
        allowed_ids=ctx.allowed_workspace_ids,
        workspace_id=workspace_id,
    )


@router.post("/users", response_model=UserRead, status_code=201)
async def create_user_endpoint(body: UserCreate, ctx: ContextDep, session: SessionDep) -> User:
    require_org_admin(ctx)
    return await tenancy_service.create_user(
        session, org_id=ctx.org_id, email=body.email, name=body.name
    )


@router.post("/memberships", response_model=MembershipRead, status_code=201)
async def create_membership(
    body: MembershipCreate, ctx: ContextDep, session: SessionDep
) -> Membership:
    require_org_admin(ctx)
    return await tenancy_service.add_membership(
        session,
        org_id=ctx.org_id,
        user_id=body.user_id,
        scope=body.scope,
        role=body.role,
        workspace_id=body.workspace_id,
    )
