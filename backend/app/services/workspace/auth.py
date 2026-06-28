"""Auth: LinkedIn sign-in via Unipile hosted-auth + the sealed session cookie.

Sign-in connects a real LinkedIn account through Unipile's hosted-auth wizard; the connected
account's stable identity (`member_urn`) maps to a local user, provisioning an org + default
workspace on first login. The session is a Fernet-sealed cookie holding the local user id.
Email/password sign-in (the seeded demo account, scrypt-hashed) and an X-User-Id header (no-SSO
only, for tests) round out the sign-in methods.
"""

from functools import lru_cache

from fastapi import Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from workos import WorkOSClient

from app.core.config import get_settings
from app.core.crypto import seal, unseal, verify_password
from app.core.db import new_id
from app.ext.unipile import UnipileConnection, unipile_connection
from app.models import (
    ConnectionProvider,
    LoginAttempt,
    SeatType,
    User,
)
from app.services.workspace.connections import (
    provision_from_linkedin,
    provision_user,
    upsert_seat,
)


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


# Friendly IdP hint from the login screen → WorkOS OAuth provider, so the Google / Microsoft
# buttons deep-link straight to that provider. Anything else lands on AuthKit's own chooser.
_OAUTH_PROVIDERS = {
    "google": "GoogleOAuth",
    "microsoft": "MicrosoftOAuth",
}


def workos_login_url(state: str | None = None, *, idp: str | None = None) -> str | None:
    """The AuthKit authorization URL, or None if WorkOS isn't configured.

    `idp` ("google" / "microsoft") deep-links straight to that OAuth provider; anything else
    (including None) lands on AuthKit's hosted provider chooser (Google / Microsoft / email).
    """
    s = get_settings()
    if not s.workos_enabled:
        return None
    return _workos_client().user_management.get_authorization_url(
        provider=_OAUTH_PROVIDERS.get(idp or "", "authkit"),
        redirect_uri=s.workos_redirect_uri,
        state=state,
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


async def start_seat_connect(session: AsyncSession, *, user_id: str) -> str | None:
    """Begin a hosted-auth wizard for an already-signed-in user to connect (or reconnect) their
    LinkedIn seat; returns the wizard URL (None if Unipile/webhook isn't configured). The seat is
    attached server-side when the notify webhook fires — see `complete_linkedin_notify`.
    """
    conn = unipile_connection()
    s = get_settings()
    if conn is None or not s.unipile_webhook_secret:
        return None
    state = new_id()
    # Pre-naming the user is what tells the shared notify this is a seat connect, not a sign-in.
    session.add(LoginAttempt(state=state, status="pending", user_id=user_id))
    await session.flush()
    notify = f"{s.api_base_url}/auth/linkedin/notify?token={s.unipile_webhook_secret}"
    redirect = f"{s.frontend_url}/settings?connected=linkedin"
    return await conn.create_link(user_ref=state, notify_url=notify, redirect_url=redirect)


async def _ensure_unipile_webhooks(conn: UnipileConnection) -> None:
    """Best-effort: make sure Unipile will deliver inbound replies + account-status events for the
    connected seat to our public receiver. Idempotent; a failure just means we retry next connect.
    """
    s = get_settings()
    if not s.unipile_webhook_secret:
        return
    receiver = f"{s.api_base_url}/webhooks/unipile?token={s.unipile_webhook_secret}"
    try:
        await conn.ensure_webhooks(request_url=receiver, sources=("messaging", "account"))
    except Exception:
        pass


async def complete_linkedin_notify(session: AsyncSession, *, state: str, account_id: str) -> None:
    """Unipile notify webhook, shared by two flows and told apart by whether the attempt already
    names a user: a *sign-in* (no user yet → provision/find them by LinkedIn identity, mark ready
    for the browser callback) or a settings-initiated *seat connect* (user preset → just attach the
    seat to them and consume the attempt, since connect has no browser-side finish step).
    """
    attempt = (
        await session.execute(select(LoginAttempt).where(LoginAttempt.state == state))
    ).scalar_one_or_none()
    if attempt is None:
        return
    conn = unipile_connection()
    profile = await conn.profile(account_id=account_id) if conn is not None else None
    name = " ".join(filter(None, [_opt(profile, "first_name"), _opt(profile, "last_name")]))
    if attempt.user_id is not None:
        user = await session.get(User, attempt.user_id)
        if user is not None:
            seat = await upsert_seat(
                session,
                organization_id=user.organization_id,
                user_id=user.id,
                provider=ConnectionProvider.linkedin,
                account_id=account_id,
                seat_type=SeatType.recruiter,
            )
            if name:
                seat.capabilities = {**(seat.capabilities or {}), "display_name": name}
            if conn is not None:
                await _ensure_unipile_webhooks(conn)
        await session.delete(attempt)
        await session.flush()
        return
    member_urn = _opt(profile, "member_urn") or account_id
    user = await provision_from_linkedin(
        session,
        member_urn=member_urn,
        name=name,
        email=_opt(profile, "email"),
        account_id=account_id,
    )
    if conn is not None:
        await _ensure_unipile_webhooks(conn)
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
    """Identify the caller: the sealed session cookie, then (no-SSO only) the X-User-Id header."""
    settings = get_settings()
    sealed = request.cookies.get(settings.session_cookie_name)
    if sealed:
        user_id = await _session_user_id(session, sealed)
        if user_id:
            return user_id
    if settings.header_auth_enabled:
        header_id = request.headers.get("X-User-Id")
        if header_id:
            user = await session.get(User, header_id)
            if user is not None:
                return user.id
    return None


# --- Email/password login ----------------------------------------------------


async def password_login(session: AsyncSession, *, email: str, password: str) -> str | None:
    """Verify an email + password against the user's stored hash (SSO-only users have none)."""
    user = (
        (await session.execute(select(User).where(User.email == email).limit(1))).scalars().first()
    )
    if user is None or user.password_hash is None:
        return None
    return user.id if verify_password(password, user.password_hash) else None
