"""Auth: WorkOS AuthKit integration (service) + HTTP endpoints.

The sealed session is a Fernet-encrypted cookie holding the WorkOS access/refresh tokens. We map
the WorkOS user (by id, stored on `User.sso_subject`) to a local user; first login provisions an
organization + default workspace + org-admin membership.
"""

from functools import lru_cache

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from workos import WorkOSClient
from workos.session import seal_session_from_auth_response
from workos.user_management import AuthenticateResponse

from app.core.config import get_settings
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
from app.workspace.tenancy import ContextDep, SessionDep

router = APIRouter(prefix="/auth", tags=["auth"])


@lru_cache
def _client() -> WorkOSClient:
    s = get_settings()
    if not s.auth_enabled:
        raise RuntimeError("WorkOS is not configured")
    return WorkOSClient(api_key=s.workos_api_key, client_id=s.workos_client_id)


def login_url(state: str | None = None) -> str:
    s = get_settings()
    return _client().user_management.get_authorization_url(
        provider="authkit", redirect_uri=s.workos_redirect_uri, state=state
    )


def _seal(resp: AuthenticateResponse) -> str:
    user = resp.user
    # Runtime fallback for older WorkOS user shapes; the stub doesn't model the mapping form.
    user_dict = user.model_dump() if hasattr(user, "model_dump") else dict(user)  # type: ignore[call-overload]
    return seal_session_from_auth_response(
        access_token=resp.access_token,
        refresh_token=resp.refresh_token,
        user=user_dict,
        cookie_password=get_settings().session_cookie_password,
    )


async def complete_login(session: AsyncSession, *, code: str) -> tuple[User, str]:
    """Exchange an AuthKit code → provision/find the local user → return (user, sealed cookie)."""
    resp = _client().user_management.authenticate_with_code(code=code)
    wos_user = resp.user
    first = getattr(wos_user, "first_name", None) or ""
    last = getattr(wos_user, "last_name", None) or ""
    name = f"{first} {last}".strip() or wos_user.email
    user = await _provision(
        session,
        wos_user_id=wos_user.id,
        email=wos_user.email,
        name=name,
        wos_org_id=getattr(resp, "organization_id", None),
    )
    return user, _seal(resp)


async def _provision(
    session: AsyncSession, *, wos_user_id: str, email: str, name: str, wos_org_id: str | None
) -> User:
    existing = (
        await session.execute(select(User).where(User.sso_subject == wos_user_id))
    ).scalar_one_or_none()
    if existing is not None:
        return existing

    org: Organization | None = None
    if wos_org_id:
        org = (
            await session.execute(
                select(Organization).where(Organization.workos_org_id == wos_org_id)
            )
        ).scalar_one_or_none()
    if org is None:
        domain = email.split("@")[-1].split(".")[0] if "@" in email else "workspace"
        org = Organization(
            name=domain.capitalize(),
            slug=f"{domain}-{new_id()[:8].lower()}",
            workos_org_id=wos_org_id,
        )
        session.add(org)
        await session.flush()
        session.add(
            Workspace(organization_id=org.id, name="Default workspace", kind=WorkspaceKind.team)
        )
        await session.flush()

    user = User(organization_id=org.id, email=email, name=name, sso_subject=wos_user_id)
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            user_id=user.id,
            organization_id=org.id,
            scope=MembershipScope.organization,
            role=MembershipRole.org_admin,
        )
    )
    await session.flush()
    return user


async def resolve_user_id(session: AsyncSession, sealed: str) -> tuple[str | None, str | None]:
    """Validate the sealed cookie → return (local user_id, refreshed cookie or None).

    The refreshed cookie is non-None when the access token had expired and was renewed; the caller
    should write it back so the session stays alive.
    """
    s = get_settings()
    try:
        sess = _client().user_management.load_sealed_session(
            session_data=sealed, cookie_password=s.session_cookie_password
        )
        result = sess.authenticate()
        refreshed: str | None = None
        if getattr(result, "authenticated", False):
            wos_user = getattr(result, "user", None) or {}
        else:
            renew = sess.refresh(cookie_password=s.session_cookie_password)
            if not getattr(renew, "authenticated", False):
                return None, None
            refreshed = getattr(renew, "sealed_session", None)
            wos_user = getattr(renew, "user", None) or {}
        raw_id = wos_user.get("id") if isinstance(wos_user, dict) else getattr(wos_user, "id", None)
        wos_user_id = raw_id if isinstance(raw_id, str) else None
        if not wos_user_id:
            return None, None
        user = (
            await session.execute(select(User).where(User.sso_subject == wos_user_id))
        ).scalar_one_or_none()
        return (user.id if user else None), refreshed
    except Exception:
        return None, None


def logout_url(sealed: str | None) -> str:
    s = get_settings()
    if not sealed:
        return s.frontend_url
    try:
        sess = _client().user_management.load_sealed_session(
            session_data=sealed, cookie_password=s.session_cookie_password
        )
        return sess.get_logout_url(return_to=s.frontend_url)
    except Exception:
        return s.frontend_url


def set_session_cookie(response: Response, sealed: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.session_cookie_name,
        value=sealed,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 14,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=get_settings().session_cookie_name, path="/")


# --- Dev login (no WorkOS) ---------------------------------------------------


async def ensure_demo_user(session: AsyncSession) -> User:
    """Return the demo admin (created by the seeder), or provision a minimal one on the fly."""
    email = get_settings().demo_admin_email
    user = (
        (await session.execute(select(User).where(User.email == email).limit(1))).scalars().first()
    )
    if user is not None:
        return user
    org = Organization(name="Demo", slug=f"demo-{new_id()[:8].lower()}")
    session.add(org)
    await session.flush()
    session.add(
        Workspace(organization_id=org.id, name="Default workspace", kind=WorkspaceKind.team)
    )
    user = User(organization_id=org.id, email=email, name="Demo Admin")
    session.add(user)
    await session.flush()
    session.add(
        Membership(
            user_id=user.id,
            organization_id=org.id,
            scope=MembershipScope.organization,
            role=MembershipRole.org_admin,
        )
    )
    await session.flush()
    return user


def set_dev_cookie(response: Response, user_id: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.dev_session_cookie_name,
        value=user_id,
        httponly=True,
        secure=s.cookie_secure,
        samesite="lax",
        path="/",
        max_age=60 * 60 * 24 * 7,
    )


def clear_dev_cookie(response: Response) -> None:
    response.delete_cookie(key=get_settings().dev_session_cookie_name, path="/")


# --- Endpoints ---------------------------------------------------------------


@router.get("/login")
async def login() -> RedirectResponse:
    if not get_settings().auth_enabled:
        raise HTTPException(status_code=503, detail="WorkOS is not configured")
    return RedirectResponse(login_url())


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
    user = await ensure_demo_user(session)
    set_dev_cookie(response, user.id)
    return DevLoginResponse(user=UserSummary(id=user.id, email=user.email, name=user.name))


@router.get("/callback")
async def callback(code: str, session: SessionDep) -> RedirectResponse:
    settings = get_settings()
    try:
        _user, sealed = await complete_login(session, code=code)
    except Exception:
        return RedirectResponse(f"{settings.frontend_url}/login?error=auth_failed")
    redirect = RedirectResponse(settings.frontend_url)
    set_session_cookie(redirect, sealed)
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
    url = logout_url(sealed) if settings.auth_enabled else settings.frontend_url
    clear_session_cookie(response)
    clear_dev_cookie(response)
    return {"logout_url": url}
