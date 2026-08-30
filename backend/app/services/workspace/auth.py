"""Auth: the two sign-in routes, self-serve signup, and the sealed session cookie.

Two ways in, both landing on the same local `User` and the same session:
  * **Google / Microsoft OAuth**, brokered by WorkOS and keyed on the WorkOS user id. Each button
    pins its provider — AuthKit's own picker (enterprise SSO, email magic-link) is not offered.
  * **Email + password** — self-serve signup (scrypt-hashed), gated on email verification.

LinkedIn is deliberately *not* one of them: it is connected from Settings as a sending seat, by
someone already signed in. That flow lives in `workspace/connections.py`, with the other seats —
nothing about it belongs here.

First arrival by either route provisions an org + default workspace. The session itself is a
Fernet-sealed cookie holding the user id and their session epoch, so a password reset can retire
every outstanding cookie at once. An X-User-Id header stands in for all of it in local/test runs
with no provider configured (see `Settings.header_auth_enabled`).
"""

import hmac
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from hashlib import sha256

from fastapi import HTTPException, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from workos import WorkOSClient

from app.core.config import get_settings
from app.core.crypto import hash_password, seal, sign, unseal, verify, verify_password
from app.core.db import new_id
from app.models import Organization, User, UserStatus
from app.services.workspace import connections
from app.services.workspace import tenancy as tenancy_service
from app.services.workspace.connections import provision_user
from app.services.workspace.email_templates import (
    invite_email,
    password_reset_email,
    verification_email,
)
from app.services.workspace.mailer import send_transactional

# --- Google / Microsoft OAuth (brokered by WorkOS) ---------------------------


@lru_cache
def _workos_client() -> WorkOSClient:
    s = get_settings()
    return WorkOSClient(api_key=s.workos_api_key, client_id=s.workos_client_id)


# The identity providers we actually offer, mapped to WorkOS's own provider identifiers. Anything
# not in here (enterprise SSO connections, AuthKit's email magic-link) is deliberately unreachable.
WORKOS_PROVIDERS = {"google": "GoogleOAuth", "microsoft": "MicrosoftOAuth"}


def workos_login_url(provider: str, *, state: str | None = None) -> str | None:
    """The authorization URL for one pinned provider, or None if it isn't offered/configured.

    Pinning matters: `provider="authkit"` sends the browser to AuthKit's *picker*, which offers
    enterprise SSO and an email magic-link alongside Google and Microsoft. So a "Continue with
    Google" button wired that way didn't go to Google, and removing the SSO button from our own
    screen wouldn't have removed SSO — it was still one click further in.
    """
    s = get_settings()
    workos_provider = WORKOS_PROVIDERS.get(provider)
    if workos_provider is None or not s.workos_enabled:
        return None
    return _workos_client().user_management.get_authorization_url(
        provider=workos_provider, redirect_uri=s.workos_redirect_uri, state=state
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


SESSION_TTL_SECONDS = 60 * 60 * 24 * 14  # matches the cookie's own max-age


def mint_session(user_id: str, *, epoch: int = 0) -> str:
    """Seal a local user id (and its session epoch) into the session-cookie value."""
    return seal(f"{user_id}|{epoch}")


def mint_session_for(user: User) -> str:
    return mint_session(user.id, epoch=user.session_epoch)


async def _session_user_id(session: AsyncSession, sealed: str) -> str | None:
    try:
        payload = unseal(sealed, ttl_seconds=SESSION_TTL_SECONDS)
    except Exception:
        return None  # tampered, minted with a retired key, or simply too old
    user_id, _, epoch = payload.partition("|")
    if not epoch.isdigit():
        return None  # pre-epoch cookie format — no longer trusted
    user = await session.get(User, user_id)
    if user is None or user.session_epoch != int(epoch):
        return None  # the account revoked its sessions (password reset)
    return user.id


def set_session_cookie(response: Response, sealed: str) -> None:
    s = get_settings()
    response.set_cookie(
        key=s.session_cookie_name,
        value=sealed,
        httponly=True,
        secure=s.cookie_secure,
        samesite=s.cookie_samesite,
        path="/",
        max_age=SESSION_TTL_SECONDS,
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(key=get_settings().session_cookie_name, path="/")


# --- request → user id -------------------------------------------------------


async def resolve_user_from_request(request: Request, session: AsyncSession) -> str | None:
    """Identify the caller: the sealed session cookie, then the X-User-Id header where that
    stand-in is enabled (local/test with no provider configured)."""
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

# A real hash to check against when no account matches, so a miss costs the same as a hit —
# without it, response time tells an attacker which addresses are registered.
_DUMMY_HASH = hash_password("sourcewell-no-such-user")


def normalize_email(value: str) -> str:
    """Addresses are stored lowercase; sign-in must match how signup wrote them."""
    return value.strip().lower()


async def user_by_email(session: AsyncSession, email: str) -> User | None:
    """The single way to resolve an address to a user — always through `normalize_email`."""
    return (
        (await session.execute(select(User).where(User.email == normalize_email(email)).limit(1)))
        .scalars()
        .first()
    )


@dataclass(frozen=True)
class LoginOutcome:
    """Why a sign-in did or didn't work. `error` is the wire code the client branches on."""

    user_id: str | None = None
    error: str | None = None  # invalid | locked | email_not_verified | account_disabled
    retry_after_s: int = 0


async def _register_failure(session: AsyncSession, user: User) -> None:
    """Count a bad attempt, locking the account once they pile up.

    Committed here on purpose: the handler answers a failed sign-in with a 4xx, and
    `get_session` rolls the request back on any raise — a flush alone would be thrown away and
    the lockout would never fire.
    """
    s = get_settings()
    user.failed_login_count += 1
    if user.failed_login_count >= s.login_max_attempts:
        user.locked_until = datetime.now(UTC) + timedelta(minutes=s.login_lockout_minutes)
        user.failed_login_count = 0
    await session.commit()


async def password_login(session: AsyncSession, *, email: str, password: str) -> LoginOutcome:
    """Verify an email + password. Throttled per account; OAuth-only users have no hash to match."""
    user = await user_by_email(session, email)
    if user is None:
        verify_password(password, _DUMMY_HASH)  # equalise timing
        return LoginOutcome(error="invalid")

    now = datetime.now(UTC)
    if user.locked_until is not None and user.locked_until > now:
        return LoginOutcome(
            error="locked", retry_after_s=int((user.locked_until - now).total_seconds()) + 1
        )

    if not verify_password(password, user.password_hash or _DUMMY_HASH):
        await _register_failure(session, user)
        return LoginOutcome(error="invalid")

    # Correct password — only now is it safe to say anything about the account's state.
    if user.status is UserStatus.disabled:
        return LoginOutcome(error="account_disabled")
    if user.email_verified_at is None:
        return LoginOutcome(error="email_not_verified")

    user.failed_login_count = 0
    user.locked_until = None
    await session.flush()
    return LoginOutcome(user_id=user.id)


# --- Password reset ----------------------------------------------------------
#
# The token embeds a fingerprint of the current password hash, so it dies the moment the password
# changes — one reset per link, no server-side state.

_RESET_PREFIX = "password-reset"


def _hash_fingerprint(password_hash: str | None) -> str:
    return sha256((password_hash or "none").encode()).hexdigest()[:16]


def password_reset_token(user: User, *, ttl_minutes: int | None = None) -> str:
    minutes = get_settings().password_reset_ttl_minutes if ttl_minutes is None else ttl_minutes
    return _mint_link_token(
        _RESET_PREFIX,
        fields=[user.id, _hash_fingerprint(user.password_hash)],
        expires_in=timedelta(minutes=minutes),
    )


def password_reset_url(user: User) -> str:
    base = get_settings().frontend_url.rstrip("/")
    return f"{base}/reset-password?token={password_reset_token(user)}"


def _may_set_password(user: User) -> bool:
    """Whether we will mail this account a link that sets a password.

    Two kinds of account qualify: one that already has a password (an ordinary reset), and one
    whose address is already proven but has no password yet — an invited teammate who accepted, or
    someone who has only ever signed in with Google. Without the second case a teammate with no
    Google or Microsoft account had exactly one way in ever: the invitation link, and nothing after
    it expired.

    The `email_verified_at` half is what keeps this from becoming another capture door. A row whose
    address nobody has proven — an unaccepted invite — must not be reachable here, or "forgot
    password" would hand the real owner of that mailbox an account inside a stranger's org.
    """
    return user.password_hash is not None or user.email_verified_at is not None


async def request_password_reset(session: AsyncSession, *, email: str) -> None:
    """Mail a link to set this account's password. Silent either way — the endpoint must not
    reveal who has an account."""
    user = await user_by_email(session, email)
    if user is None or user.status is UserStatus.disabled or not _may_set_password(user):
        return
    mail = password_reset_email(
        first_name=user.first_name or user.name.split(" ")[0],
        url=password_reset_url(user),
        ttl_minutes=get_settings().password_reset_ttl_minutes,
        # No password yet: this is "choose one", not "reset the one you have".
        first_time=user.password_hash is None,
    )
    await send_transactional(to=user.email, mail=mail)


async def reset_password(session: AsyncSession, *, token: str, password: str) -> User | None:
    """Consume a reset link → the user, with the new password set (None if the link is spent)."""
    fields = _read_link_token(token, _RESET_PREFIX, field_count=2)
    if fields is None:
        return None
    user_id, fingerprint = fields
    user = await session.get(User, user_id)
    if user is None or user.status is UserStatus.disabled:
        return None
    if not hmac.compare_digest(fingerprint, _hash_fingerprint(user.password_hash)):
        return None  # the password already changed — this link is spent

    user.password_hash = hash_password(password)
    user.failed_login_count = 0
    user.locked_until = None
    # Whoever was signed in with the old password — including anyone who stole a session — is
    # signed out by the reset. The fresh cookie below is the only one that still resolves.
    user.session_epoch += 1
    # Receiving the link proves control of the address, so a pending signup is confirmed too.
    user.email_verified_at = user.email_verified_at or datetime.now(UTC)
    await session.flush()
    return user


# --- Self-serve signup -------------------------------------------------------

# Kept in sync with the signup form's client-side validation (frontend/src/pages/signup-page.tsx).
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{2,29}$")
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
MIN_PASSWORD_LEN = 8


def slugify(value: str) -> str:
    """Company name → URL slug ("Acme Talent Co." → "acme-talent-co")."""
    slug = re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")
    return slug[:80] or "org"


async def _slug_free(session: AsyncSession, slug: str) -> bool:
    return (
        await session.execute(select(Organization.id).where(Organization.slug == slug).limit(1))
    ).scalar_one_or_none() is None


async def _unique_slug(session: AsyncSession, company_name: str) -> str:
    """A free org slug for this company name, disambiguated when the name is already taken.

    The suffix comes from the *tail* of a fresh id — a ULID's leading characters encode the
    timestamp, so two signups a minute apart would otherwise draw the same "unique" suffix.
    """
    base = slugify(company_name)
    if await _slug_free(session, base):
        return base
    for _ in range(5):
        candidate = f"{base}-{new_id()[-6:].lower()}"
        if await _slug_free(session, candidate):
            return candidate
    return f"{base}-{new_id().lower()}"


async def signup_with_password(
    session: AsyncSession,
    *,
    first_name: str,
    last_name: str,
    username: str,
    email: str,
    company_name: str,
    avatar_url: str | None,
    password: str,
) -> User:
    """Self-serve signup: a new organization + its first admin, from the signup form.

    Email and username are both unique across the install — identity is global, and
    `password_login` resolves a user by address alone. The checks below turn the database's own
    constraints into a 409 the signup form can render against the offending field.
    """
    email = email.strip().lower()
    username = username.strip().lower()
    if (
        await session.execute(select(User.id).where(User.email == email).limit(1))
    ).scalar_one_or_none() is not None:
        raise HTTPException(status_code=409, detail="That email is already registered")
    if await username_taken(session, username):
        raise HTTPException(status_code=409, detail="That username is taken")

    _, user = await tenancy_service.signup(
        session,
        org_name=company_name.strip(),
        slug=await _unique_slug(session, company_name),
        admin_email=email,
        admin_name=f"{first_name.strip()} {last_name.strip()}".strip(),
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        username=username,
        avatar_url=avatar_url,
        password_hash=hash_password(password),
    )
    return user


async def username_taken(
    session: AsyncSession, username: str, *, exclude_user_id: str | None = None
) -> bool:
    """Is this username already someone else's?

    Usernames are unique across the install, so both doors check it: signup before creating
    anything, and the OAuth completion form for a user who already exists — which is what
    `exclude_user_id` is for, so their own row doesn't count against them.
    """
    stmt = select(User.id).where(User.username == username.strip().lower())
    if exclude_user_id is not None:
        stmt = stmt.where(User.id != exclude_user_id)
    return (await session.execute(stmt.limit(1))).scalar_one_or_none() is not None


async def complete_signup_profile(
    session: AsyncSession,
    *,
    user: User,
    first_name: str,
    last_name: str,
    username: str,
    company_name: str,
    avatar_url: str | None,
) -> User:
    """Fill in the profile an OAuth sign-in couldn't supply, and name the org properly.

    Google and Microsoft hand over an address and a display name; everything else the product
    needs — username, company, avatar — has to be asked for. The account already exists and is
    already verified by the provider at this point, so this is the last step of *signup*, not a
    settings edit: it runs once, and `profile_completed_at` is what closes it.

    The org was provisioned under a placeholder taken from the email domain, so it is renamed
    (and re-slugged) here from the company name the user actually typed.
    """
    if user.profile_completed_at is not None:
        raise HTTPException(status_code=409, detail="This profile is already complete")
    username = username.strip().lower()
    if await username_taken(session, username, exclude_user_id=user.id):
        raise HTTPException(status_code=409, detail="That username is taken")

    first_name, last_name = first_name.strip(), last_name.strip()
    user.first_name = first_name
    user.last_name = last_name
    user.name = f"{first_name} {last_name}".strip() or user.name
    user.username = username
    if avatar_url:
        user.avatar_url = avatar_url
    user.profile_completed_at = datetime.now(UTC)

    org_id = await connections.home_org_id(session, user_id=user.id)
    org = await session.get(Organization, org_id) if org_id else None
    if org is not None:
        org.name = company_name.strip()
        org.slug = await _unique_slug(session, company_name)
    await session.flush()
    return user


# --- Email verification ------------------------------------------------------
#
# The token is a self-contained signed payload (same HMAC scheme as unsubscribe links) — no table,
# no cleanup job. Its signature and expiry are the whole gate: a still-valid link keeps working
# after the address is confirmed, because mail scanners click it before the recipient does (see
# `confirm_verification`).

_VERIFY_PREFIX = "verify-email"


def _mint_link_token(prefix: str, *, fields: list[str], expires_in: timedelta) -> str:
    """`prefix|field|…|expiry`, HMAC-signed. The prefix keeps one link type from being replayed
    as another (they share a signing key with unsubscribe links)."""
    expires = int((datetime.now(UTC) + expires_in).timestamp())
    return sign("|".join([prefix, *fields, str(expires)]))


def _read_link_token(token: str, prefix: str, *, field_count: int) -> list[str] | None:
    """The fields inside a valid, unexpired token of this type; None if forged or stale."""
    payload = verify(token)
    if payload is None:
        return None
    parts = payload.split("|")
    if len(parts) != field_count + 2 or parts[0] != prefix:
        return None
    try:
        if datetime.now(UTC).timestamp() > float(parts[-1]):
            return None
    except ValueError:
        return None
    return parts[1:-1]


def verification_token(user_id: str, *, ttl_hours: int | None = None) -> str:
    hours = get_settings().email_verification_ttl_hours if ttl_hours is None else ttl_hours
    return _mint_link_token(_VERIFY_PREFIX, fields=[user_id], expires_in=timedelta(hours=hours))


def parse_verification_token(token: str) -> str | None:
    """The user id inside a valid, unexpired token; None if tampered with or stale."""
    fields = _read_link_token(token, _VERIFY_PREFIX, field_count=1)
    return fields[0] if fields else None


def verification_url(user_id: str) -> str:
    base = get_settings().api_base_url.rstrip("/")
    return f"{base}/auth/verify?token={verification_token(user_id)}"


async def send_verification_email(user: User) -> bool:
    """Mail the confirmation link. False when the mail hop failed (the UI offers a resend)."""
    s = get_settings()
    mail = verification_email(
        first_name=user.first_name or user.name.split(" ")[0],
        url=verification_url(user.id),
        ttl_hours=s.email_verification_ttl_hours,
    )
    return await send_transactional(to=user.email, mail=mail)


async def confirm_verification(session: AsyncSession, *, token: str) -> User | None:
    """Consume a verification token → the verified user (None if forged, expired or disabled).

    A still-valid token is honoured even when the address is *already* confirmed, rather than
    reported as a spent link. Corporate mail security (Defender Safe Links, Proofpoint) fetches
    every URL in an inbound message before the recipient sees it, so the scanner's GET was landing
    first: it confirmed the account, threw away the session cookie, and the person's own click then
    hit "that link has expired" — on a brand-new account, with a resend button that does nothing
    because they are, in fact, verified. Nothing is granted twice here; the signature and expiry
    are what gate it, and they still hold.
    """
    user_id = parse_verification_token(token)
    if user_id is None:
        return None
    user = await session.get(User, user_id)
    if user is None or user.status is UserStatus.disabled:
        return None
    if user.email_verified_at is None:
        user.email_verified_at = datetime.now(UTC)
        await session.flush()
    return user


async def resend_verification(session: AsyncSession, *, email: str) -> None:
    """Re-send the link if that address has a pending signup. Silent either way — the endpoint
    must not reveal whether an address is registered."""
    user = await user_by_email(session, email)
    if user is not None and user.email_verified_at is None:
        await send_verification_email(user)


# --- Team invites ------------------------------------------------------------
#
# An invite writes a `User` row for someone who has not agreed to anything yet, so the row itself
# proves nothing. The emailed link is the proof: clicking it is what confirms the address, and
# until then the account cannot be signed in to and cannot be linked to an OAuth identity (see
# `connections.provision_user`). Same signed-token scheme as the other account links — no table.

_INVITE_PREFIX = "invite"


def invite_token(user_id: str, *, ttl_hours: int | None = None) -> str:
    hours = get_settings().invite_ttl_hours if ttl_hours is None else ttl_hours
    return _mint_link_token(_INVITE_PREFIX, fields=[user_id], expires_in=timedelta(hours=hours))


def invite_url(user_id: str) -> str:
    base = get_settings().api_base_url.rstrip("/")
    return f"{base}/auth/invite?token={invite_token(user_id)}"


async def send_invite_email(
    session: AsyncSession, *, user: User, inviter: User | None, organization_id: str
) -> bool:
    """Mail the invitation. False when the mail hop failed, so the caller can say so.

    The organization is passed in rather than read off the user: identity is global, so the org
    is the one the invite was issued from, not one the user happens to already belong to.
    """
    org = await session.get(Organization, organization_id)
    mail = invite_email(
        first_name=user.first_name or user.name.split(" ")[0],
        inviter=inviter.name if inviter is not None else "",
        org_name=org.name if org is not None else "Sourcewell",
        url=invite_url(user.id),
        ttl_hours=get_settings().invite_ttl_hours,
    )
    return await send_transactional(to=user.email, mail=mail)


async def accept_invite(session: AsyncSession, *, token: str) -> User | None:
    """Consume an invitation link → the now-active member (None if forged, expired or revoked).

    Like the signup confirmation, a still-valid link keeps working after the first click: mail
    scanners fetch it before the recipient does, and refusing the second click would strand a
    brand-new teammate. What it never does is *re-activate* — a member who was disabled since
    being invited stays out.
    """
    fields = _read_link_token(token, _INVITE_PREFIX, field_count=1)
    if fields is None:
        return None
    user = await session.get(User, fields[0])
    if user is None or user.status is UserStatus.disabled:
        return None
    now = datetime.now(UTC)
    # Clicking proves control of the mailbox — the whole point of the link.
    user.email_verified_at = user.email_verified_at or now
    if user.status is UserStatus.invited:
        user.status = UserStatus.active
    # They joined an org that already exists: no company to name, no password to choose.
    user.profile_completed_at = user.profile_completed_at or now
    await session.flush()
    return user
