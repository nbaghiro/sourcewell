"""Auth: LinkedIn sign-in via Unipile hosted-auth + the sealed session cookie.

Sign-in connects a real LinkedIn account through Unipile's hosted-auth wizard; the connected
account's stable identity (`member_urn`) maps to a local user, provisioning an org + default
workspace on first login. The session is a Fernet-sealed cookie holding the local user id. A
dev-login bypass (header / cookie) is available when LinkedIn auth isn't configured (local / QA).
"""

from functools import lru_cache

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from workos import WorkOSClient

from app.core.config import get_settings
from app.core.crypto import seal, unseal
from app.core.db import new_id
from app.ext.unipile import unipile_connection
from app.models import (
    LoginAttempt,
    Membership,
    MembershipRole,
    MembershipScope,
    Organization,
    User,
    Workspace,
    WorkspaceKind,
)
from app.services.workspace.connections import provision_from_linkedin, provision_user


def _opt(payload: object, key: str) -> str | None:
    if isinstance(payload, dict):
        value = payload.get(key)
        return value if isinstance(value, str) and value else None
    return None


# --- WorkOS AuthKit (SSO: Google / Microsoft / email) ------------------------


@lru_cache
def _workos_client() -> WorkOSClient:
    s = get_settings()
    return WorkOSClient(api_key=s.workos_api_key, client_id=s.workos_client_id)


def workos_login_url(state: str | None = None) -> str | None:
    """The AuthKit authorization URL, or None if WorkOS isn't configured."""
    s = get_settings()
    if not s.workos_enabled:
        return None
    return _workos_client().user_management.get_authorization_url(
        provider="authkit", redirect_uri=s.workos_redirect_uri, state=state
    )


async def complete_workos_login(session: AsyncSession, *, code: str) -> str | None:
    """Exchange an AuthKit code → provision/find the local user → their id (None on failure)."""
    try:
        resp = _workos_client().user_management.authenticate_with_code(code=code)
    except Exception:
        return None
    wos_user = resp.user
    first = getattr(wos_user, "first_name", None) or ""
    last = getattr(wos_user, "last_name", None) or ""
    name = f"{first} {last}".strip() or wos_user.email
    return (await provision_user(session, subject=wos_user.id, name=name, email=wos_user.email)).id


# --- the sealed session ------------------------------------------------------


def mint_session(user_id: str) -> str:
    """Seal a local user id into the session-cookie value."""
    return seal(user_id)


async def _session_user_id(session: AsyncSession, sealed: str) -> str | None:
    try:
        user_id = unseal(sealed)
    except Exception:
        return None
    user = await session.get(User, user_id)
    return user.id if user is not None else None


def set_session_cookie(response: Response, sealed: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.session_cookie_name,
        value=sealed,
        httponly=True,
        secure=s.cookie_secure,
        samesite=s.cookie_samesite,
        path="/",
        max_age=60 * 60 * 24 * 14,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=get_settings().session_cookie_name, path="/")


# --- LinkedIn (Unipile hosted-auth) sign-in ----------------------------------


async def start_linkedin_login(session: AsyncSession) -> str | None:
    """Create a hosted-auth wizard link for a sign-in; returns its URL (None if unconfigured)."""
    conn = unipile_connection()
    if conn is None:
        return None
    s = get_settings()
    state = new_id()
    session.add(LoginAttempt(state=state, status="pending"))
    await session.flush()
    notify = f"{s.api_base_url}/auth/linkedin/notify?token={s.unipile_webhook_secret}"
    redirect = f"{s.api_base_url}/auth/callback?state={state}"
    return await conn.create_link(user_ref=state, notify_url=notify, redirect_url=redirect)


async def complete_linkedin_notify(session: AsyncSession, *, state: str, account_id: str) -> None:
    """Unipile notify: read the connected account's identity, provision the user, mark ready."""
    attempt = (
        await session.execute(select(LoginAttempt).where(LoginAttempt.state == state))
    ).scalar_one_or_none()
    if attempt is None:
        return
    conn = unipile_connection()
    profile = await conn.profile(account_id=account_id) if conn is not None else None
    member_urn = _opt(profile, "member_urn") or account_id
    name = " ".join(filter(None, [_opt(profile, "first_name"), _opt(profile, "last_name")]))
    user = await provision_from_linkedin(
        session,
        member_urn=member_urn,
        name=name,
        email=_opt(profile, "email"),
        account_id=account_id,
    )
    attempt.user_id = user.id
    attempt.account_id = account_id
    attempt.status = "ready"
    await session.flush()


async def finish_linkedin_login(session: AsyncSession, *, state: str) -> str | None:
    """Browser callback: if the notify provisioned the user, return their id (and consume it)."""
    attempt = (
        await session.execute(select(LoginAttempt).where(LoginAttempt.state == state))
    ).scalar_one_or_none()
    if attempt is None or attempt.status != "ready" or attempt.user_id is None:
        return None
    user_id = attempt.user_id
    await session.delete(attempt)
    await session.flush()
    return user_id


# --- request → user id -------------------------------------------------------


async def resolve_user_from_request(
    request: Request, response: Response, session: AsyncSession
) -> str | None:
    """Identify the caller: the sealed LinkedIn session cookie, then the dev-login fallback."""
    settings = get_settings()
    sealed = request.cookies.get(settings.session_cookie_name)
    if sealed:
        user_id = await _session_user_id(session, sealed)
        if user_id:
            return user_id
    if settings.dev_login_enabled:
        dev_id = request.cookies.get(settings.dev_session_cookie_name)
        if dev_id is not None:
            user = await session.get(User, dev_id)
            if user is not None:
                return user.id
        header_id = request.headers.get("X-User-Id")
        if header_id:
            user = await session.get(User, header_id)
            if user is not None:
                return user.id
    return None


# --- Dev login (only when LinkedIn auth is not configured) -------------------


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
        samesite=s.cookie_samesite,
        path="/",
        max_age=60 * 60 * 24 * 7,
    )


def clear_dev_cookie(response: Response) -> None:
    response.delete_cookie(key=get_settings().dev_session_cookie_name, path="/")
