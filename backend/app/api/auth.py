"""Auth HTTP endpoints: LinkedIn (Unipile) login/notify/callback + dev login + me/logout.

Business logic (the hosted-auth link, session sealing, provisioning) lives in
`app.services.workspace.auth`; this module is the HTTP layer only.
"""

import hmac
import json

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
    """Start WorkOS SSO (Google / Microsoft / email): redirect to AuthKit."""
    url = auth_service.workos_login_url()
    if url is None:
        raise HTTPException(status_code=503, detail="SSO is not configured")
    return RedirectResponse(url)


@router.get("/linkedin/login")
async def linkedin_login(session: SessionDep) -> RedirectResponse:
    """Start a LinkedIn sign-in: redirect to the Unipile hosted-auth wizard."""
    url = await auth_service.start_linkedin_login(session)
    if url is None:
        raise HTTPException(status_code=503, detail="LinkedIn auth is not configured")
    return RedirectResponse(url)


class AuthOptions(BaseModel):
    workos: bool
    linkedin: bool
    password: bool


@router.get("/options", response_model=AuthOptions)
async def options() -> AuthOptions:
    """Which sign-in methods are available, so the login screen renders the right buttons."""
    s = get_settings()
    return AuthOptions(workos=s.workos_enabled, linkedin=s.linkedin_auth_enabled, password=True)


@router.post("/linkedin/notify")
async def linkedin_notify(request: Request, session: SessionDep) -> dict[str, str]:
    """Unipile server notify: provision the user for the connected account (token-gated)."""
    secret = get_settings().unipile_webhook_secret
    token = request.query_params.get("token") or ""
    if not secret or not hmac.compare_digest(token, secret):
        raise HTTPException(status_code=401, detail="invalid token")
    try:
        parsed: object = json.loads(await request.body() or b"{}")
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="invalid JSON") from None
    payload = parsed if isinstance(parsed, dict) else {}
    account_id = payload.get("account_id")
    state = payload.get("name")  # the state token we set as `name` on the hosted-auth link
    if isinstance(account_id, str) and isinstance(state, str):
        await auth_service.complete_linkedin_notify(session, state=state, account_id=account_id)
    return {"status": "ok"}


@router.get("/callback")
async def callback(
    session: SessionDep, code: str | None = None, state: str | None = None
) -> RedirectResponse:
    """Shared callback: WorkOS sends `code`, LinkedIn sends `state`. Mint the unified session."""
    settings = get_settings()
    user_id: str | None = None
    if code is not None:
        user_id = await auth_service.complete_workos_login(session, code=code)
    elif state is not None:
        user_id = await auth_service.finish_linkedin_login(session, state=state)
    if user_id is None:
        return RedirectResponse(f"{settings.frontend_url}/login?error=auth_failed")
    redirect = RedirectResponse(settings.frontend_url)
    auth_service.set_session_cookie(redirect, auth_service.mint_session(user_id))
    return redirect


class PasswordLoginRequest(BaseModel):
    email: str
    password: str


class UserSummary(BaseModel):
    id: str
    email: str
    name: str


class PasswordLoginResponse(BaseModel):
    user: UserSummary


@router.post("/password", response_model=PasswordLoginResponse)
async def password_login(
    session: SessionDep, response: Response, body: PasswordLoginRequest
) -> PasswordLoginResponse:
    """Email + password sign-in (the seeded demo account: demo@sourcewell.ai)."""
    user_id = await auth_service.password_login(session, email=body.email, password=body.password)
    if user_id is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    auth_service.set_session_cookie(response, auth_service.mint_session(user_id))
    return PasswordLoginResponse(user=UserSummary(id=user.id, email=user.email, name=user.name))


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
async def logout(response: Response) -> dict[str, str]:
    auth_service.clear_session_cookie(response)
    return {"logout_url": get_settings().frontend_url}
