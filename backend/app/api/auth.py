"""Auth HTTP endpoints: WorkOS AuthKit login/callback + dev login + me/logout.

Business logic (WorkOS client, session sealing, provisioning) lives in
`app.services.workspace.auth`; this module is the HTTP layer only.
"""

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select

from app.api.context import ContextDep, SessionDep
from app.core.config import get_settings
from app.models import Organization, User, Workspace
from app.services.workspace import auth as auth_service

router = APIRouter(prefix="/auth", tags=["auth"])


@router.get("/login")
async def login() -> RedirectResponse:
    if not get_settings().auth_enabled:
        raise HTTPException(status_code=503, detail="WorkOS is not configured")
    return RedirectResponse(auth_service.login_url())


class DevLoginRequest(BaseModel):
    email: str | None = None
    password: str | None = None


class UserSummary(BaseModel):
    id: str
    email: str
    name: str


class DevLoginResponse(BaseModel):
    user: UserSummary


@router.post("/dev-login", response_model=DevLoginResponse)
async def dev_login(
    session: SessionDep, response: Response, body: DevLoginRequest | None = None
) -> DevLoginResponse:
    """Demo sign-in that bypasses WorkOS (local design/QA only).

    With no body it's a one-click bypass; with email/password it validates the demo credentials.
    """
    settings = get_settings()
    if not settings.dev_login_enabled:
        raise HTTPException(status_code=403, detail="dev login disabled (WorkOS is configured)")
    if body is not None and (body.email or body.password):
        if body.email != settings.demo_admin_email or body.password != settings.demo_password:
            raise HTTPException(status_code=401, detail="invalid credentials")
    user = await auth_service.ensure_demo_user(session)
    auth_service.set_dev_cookie(response, user.id)
    return DevLoginResponse(user=UserSummary(id=user.id, email=user.email, name=user.name))


@router.get("/callback")
async def callback(code: str, session: SessionDep) -> RedirectResponse:
    settings = get_settings()
    try:
        _user, sealed = await auth_service.complete_login(session, code=code)
    except Exception:
        return RedirectResponse(f"{settings.frontend_url}/login?error=auth_failed")
    redirect = RedirectResponse(settings.frontend_url)
    auth_service.set_session_cookie(redirect, sealed)
    return redirect


class OrgSummary(BaseModel):
    id: str
    name: str


class WorkspaceSummary(BaseModel):
    id: str
    name: str
    kind: str


class MeResponse(BaseModel):
    user: UserSummary | None
    organization: OrgSummary | None
    is_org_admin: bool
    current_workspace_id: str | None
    workspaces: list[WorkspaceSummary]


@router.get("/me", response_model=MeResponse)
async def me(ctx: ContextDep, session: SessionDep) -> MeResponse:
    user = await session.get(User, ctx.user_id)
    org = await session.get(Organization, ctx.org_id)
    workspaces = (
        (
            await session.execute(
                select(Workspace)
                .where(Workspace.id.in_(ctx.allowed_workspace_ids))
                .order_by(Workspace.created_at)
            )
        )
        .scalars()
        .all()
    )
    return MeResponse(
        user=UserSummary(id=user.id, email=user.email, name=user.name) if user else None,
        organization=OrgSummary(id=org.id, name=org.name) if org else None,
        is_org_admin=ctx.is_org_admin,
        current_workspace_id=ctx.current_workspace_id,
        workspaces=[WorkspaceSummary(id=w.id, name=w.name, kind=w.kind.value) for w in workspaces],
    )


@router.post("/logout")
async def logout(request: Request, response: Response) -> dict[str, str]:
    settings = get_settings()
    sealed = request.cookies.get(settings.session_cookie_name)
    url = auth_service.logout_url(sealed) if settings.auth_enabled else settings.frontend_url
    auth_service.clear_session_cookie(response)
    auth_service.clear_dev_cookie(response)
    return {"logout_url": url}
