"""Auth HTTP endpoints: Google/Microsoft OAuth, email/password signup, verification and reset,
plus me/logout.

Business logic (the authorization URL, session sealing, provisioning) lives in
`app.services.workspace.auth`; this module is the HTTP layer only. Nothing about LinkedIn is
here — it is a sending seat connected from Settings, so both its routes live in `api/settings.py`.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, field_validator
from sqlalchemy import select

from app.api.context import SessionDep, SignupContextDep
from app.api.labels import LabelPack, label_pack
from app.api.limits import AuthLimit, enforce_address_cooldown
from app.core.config import get_settings
from app.models import Membership, Organization, User, UserStatus, Workspace, WorkspaceKind
from app.services.insights import audit
from app.services.workspace import auth as auth_service
from app.services.workspace import connections as connections_service

router = APIRouter(prefix="/auth", tags=["auth"])

# Anonymous callers create organizations here and make us send mail to addresses they may not
# own — both need tighter ceilings than the global middleware limiter.
_signup_limit = AuthLimit("signup", limit=lambda s: s.auth_signup_per_hour, window_s=3600)
_login_limit = AuthLimit("login", limit=lambda s: s.auth_login_per_5min, window_s=300)
_mail_limit = AuthLimit("auth-mail", limit=lambda s: s.auth_mail_per_hour, window_s=3600)

# Decoded ceiling for the signup avatar; the form resizes to 256px, so real uploads are ~30 KB.
MAX_AVATAR_BYTES = 1_000_000
# Raster only — `image/svg+xml` is a document format that can carry script, and the avatar is
# echoed back to other people in the org.
AVATAR_TYPES = ("image/png", "image/jpeg", "image/webp", "image/gif")


def _login_error(code: str) -> RedirectResponse:
    """These endpoints are browser destinations, not API calls — a failure has to land somewhere
    a person can read, not on a raw JSON error page."""
    return RedirectResponse(f"{get_settings().frontend_url}/login?error={code}")


OAuthProvider = Literal["google", "microsoft"]


@router.get("/login/{provider}")
async def login(provider: OAuthProvider) -> RedirectResponse:
    """Start a Google or Microsoft sign-in, brokered by WorkOS.

    Each button names its provider outright; see `workos_login_url` for why that matters.
    """
    url = auth_service.workos_login_url(provider)
    return _login_error("provider_unavailable") if url is None else RedirectResponse(url)


class AuthOptions(BaseModel):
    """Which OAuth buttons this deployment can actually offer, so the login screen doesn't render
    one that dead-ends.

    Only the brokered providers are here. Email+password needs no configuration, so it is always
    available and the form is unconditional; LinkedIn is a sending seat connected from Settings,
    never a way in.
    """

    google: bool
    microsoft: bool


@router.get("/options", response_model=AuthOptions)
async def options() -> AuthOptions:
    """Which sign-in methods are available, so the login screen renders the right buttons."""
    enabled = get_settings().workos_enabled
    return AuthOptions(google=enabled, microsoft=enabled)


@router.get("/callback")
async def callback(session: SessionDep, code: str | None = None) -> RedirectResponse:
    """The OAuth callback: exchange WorkOS's `code` and mint the session."""
    settings = get_settings()
    user_id = await auth_service.complete_workos_login(session, code=code) if code else None
    user = await session.get(User, user_id) if user_id else None
    if user is None:
        return _login_error("auth_failed")
    # A disabled account must stay out however it signs in. `password_login` refuses one already;
    # without this an admin's revocation was undone by a single OAuth round-trip, because
    # provisioning happily returns (and re-activates) the existing user.
    if user.status is UserStatus.disabled:
        return _login_error("account_disabled")
    # A returning user goes straight in; a first-time OAuth user is signed in but still owes the
    # signup profile the provider couldn't give us, so they land on the form. Either way the
    # session is minted here — the form is posted as an authenticated request.
    destination = (
        settings.frontend_url if user.profile_completed_at else f"{settings.frontend_url}/signup"
    )
    redirect = RedirectResponse(destination)
    auth_service.set_session_cookie(redirect, auth_service.mint_session_for(user))
    return redirect


class PasswordLoginRequest(BaseModel):
    email: str
    password: str


class UserSummary(BaseModel):
    id: str
    email: str
    name: str
    username: str | None = None
    avatar_url: str | None = None


class SignedInUser(BaseModel):
    """What every endpoint that leaves the caller signed in returns: password sign-in, password
    reset, and finishing an OAuth signup."""

    user: UserSummary


async def _audit(session: SessionDep, user: User, *, action: str, summary: str) -> None:
    """Record an account-level event. Org-scoped and not workspace-scoped — these happen before
    any workspace is in play, so the org comes from the user's oldest membership."""
    org_id = await connections_service.home_org_id(session, user_id=user.id)
    if org_id is None:
        return
    await audit.record(
        session,
        org_id=org_id,
        workspace_id=None,
        actor_user_id=user.id,
        action=action,
        summary=summary,
        target_type="user",
        target_id=user.id,
    )


def _summary(user: User) -> UserSummary:
    return UserSummary(
        id=user.id,
        email=user.email,
        name=user.name,
        username=user.username,
        avatar_url=user.avatar_url,
    )


class SignupProfile(BaseModel):
    """The profile fields the signup form collects, whichever door the user came through.

    Password signup posts these with an email + password; an OAuth user posts them on their own
    (`/auth/complete-profile`) — the provider already established the address, so there is nothing
    to verify and no password to set. Validation mirrors the client-side checks either way.
    """

    first_name: str
    last_name: str
    username: str
    company_name: str
    # Optional: the app falls back to initials, so nobody is blocked at signup by not having a
    # photo to hand. Whatever is supplied still has to be a real raster image.
    avatar: str | None = None

    @field_validator("first_name", "last_name", "company_name")
    @classmethod
    def _required_text(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("This field is required")
        if len(v) > 100:
            raise ValueError("Keep this under 100 characters")
        return v

    @field_validator("username")
    @classmethod
    def _valid_username(cls, v: str) -> str:
        v = v.strip().lower()
        if not auth_service.USERNAME_RE.match(v):
            raise ValueError(
                "3-30 characters: lowercase letters, numbers, dot, dash or underscore, "
                "starting with a letter or number"
            )
        return v

    @field_validator("avatar")
    @classmethod
    def _valid_avatar(cls, v: str | None) -> str | None:
        """A `data:image/...;base64,...` URL produced by the form's client-side resize."""
        if v is None or not v.strip():
            return None
        v = v.strip()
        prefix, _, payload = v.partition(",")
        media_type = prefix.removeprefix("data:").split(";")[0].lower()
        if media_type not in AVATAR_TYPES or "base64" not in prefix or not payload:
            raise ValueError("Upload a profile photo (PNG, JPG, WebP or GIF)")
        if len(payload) * 3 // 4 > MAX_AVATAR_BYTES:
            raise ValueError("That image is too large — pick one under 1 MB")
        return v


class AccountSignupRequest(SignupProfile):
    """Self-serve signup: the profile, plus the address to confirm and the password to sign in
    with afterwards."""

    email: str
    password: str

    @field_validator("email")
    @classmethod
    def _valid_email(cls, v: str) -> str:
        v = v.strip().lower()
        if not auth_service.EMAIL_RE.match(v) or len(v) > 320:
            raise ValueError("Enter a valid email address")
        return v

    @field_validator("password")
    @classmethod
    def _strong_enough(cls, v: str) -> str:
        if len(v) < auth_service.MIN_PASSWORD_LEN:
            raise ValueError(f"Use at least {auth_service.MIN_PASSWORD_LEN} characters")
        return v


class AccountSignupResponse(BaseModel):
    """No user/session here on purpose — the account is inert until the email is confirmed.

    `email_sent` is the only thing the client branches on: false means the mail hop failed and the
    confirm screen should lead with its resend button.
    """

    email: str
    email_sent: bool


@router.post(
    "/signup",
    response_model=AccountSignupResponse,
    status_code=201,
    dependencies=[Depends(_signup_limit)],
)
async def signup(session: SessionDep, body: AccountSignupRequest) -> AccountSignupResponse:
    """Self-serve signup: create the org + its first admin, then email the confirmation link.

    No session is minted here — `GET /auth/verify` is what signs the user in, so an address
    nobody controls can never reach the app.
    """
    user = await auth_service.signup_with_password(
        session,
        first_name=body.first_name,
        last_name=body.last_name,
        username=body.username,
        email=body.email,
        company_name=body.company_name,
        avatar_url=body.avatar,
        password=body.password,
    )
    sent = await auth_service.send_verification_email(user)
    return AccountSignupResponse(email=user.email, email_sent=sent)


@router.get("/verify")
async def verify_email(session: SessionDep, token: str) -> RedirectResponse:
    """The emailed confirmation link: stamp the address verified and sign the user in."""
    settings = get_settings()
    user = await auth_service.confirm_verification(session, token=token)
    if user is None:
        return RedirectResponse(f"{settings.frontend_url}/verify-email?error=link_invalid")
    redirect = RedirectResponse(f"{settings.frontend_url}/?verified=1")
    auth_service.set_session_cookie(redirect, auth_service.mint_session_for(user))
    return redirect


@router.get("/invite")
async def accept_invite(session: SessionDep, token: str) -> RedirectResponse:
    """The emailed invitation link: confirm the address, activate the member, sign them in.

    This is the *only* door into a pending invite. The row an admin created carries an address
    nobody has agreed to yet, so until this link is clicked it can't be signed in to and can't be
    linked to a Google/Microsoft identity.
    """
    settings = get_settings()
    user = await auth_service.accept_invite(session, token=token)
    if user is None:
        return _login_error("invite_invalid")
    redirect = RedirectResponse(f"{settings.frontend_url}/?invited=1")
    auth_service.set_session_cookie(redirect, auth_service.mint_session_for(user))
    return redirect


class ResendVerificationRequest(BaseModel):
    email: str


@router.post("/verify/resend", status_code=202, dependencies=[Depends(_mail_limit)])
async def resend_verification(
    session: SessionDep, body: ResendVerificationRequest
) -> dict[str, str]:
    """Re-send the confirmation link. Always 202 — it must not reveal who has an account."""
    enforce_address_cooldown("verify-mail", body.email)
    await auth_service.resend_verification(session, email=body.email)
    return {"status": "sent"}


# Outcome → HTTP. Everything an anonymous caller can trigger is either 401 (says nothing) or a
# state only the password holder can reach, so none of these leak whether an address exists.
_LOGIN_STATUS = {
    "invalid": (401, "invalid email or password"),
    "locked": (429, "too_many_attempts"),
    "email_not_verified": (403, "email_not_verified"),
    "account_disabled": (403, "account_disabled"),
}


@router.post("/password", response_model=SignedInUser, dependencies=[Depends(_login_limit)])
async def password_login(
    session: SessionDep, response: Response, body: PasswordLoginRequest
) -> SignedInUser:
    """Email + password sign-in."""
    outcome = await auth_service.password_login(session, email=body.email, password=body.password)
    if outcome.user_id is None:
        status, detail = _LOGIN_STATUS.get(outcome.error or "invalid", (401, "invalid"))
        headers = {"Retry-After": str(outcome.retry_after_s)} if outcome.error == "locked" else None
        raise HTTPException(status_code=status, detail=detail, headers=headers)
    user = await session.get(User, outcome.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="invalid email or password")
    await _audit(
        session, user, action="auth.login", summary=f"{user.email} signed in with a password"
    )
    auth_service.set_session_cookie(response, auth_service.mint_session_for(user))
    return SignedInUser(user=_summary(user))


@router.post("/complete-profile", response_model=SignedInUser)
async def complete_profile(
    body: SignupProfile, ctx: SignupContextDep, session: SessionDep
) -> SignedInUser:
    """Finish a signup that started at Google or Microsoft.

    The account already exists and its address is already verified — the provider established
    both — so this is authenticated, takes no email and no password, and can only be called while
    the profile is still outstanding.
    """
    user = await session.get(User, ctx.user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="not authenticated")
    user = await auth_service.complete_signup_profile(
        session,
        user=user,
        first_name=body.first_name,
        last_name=body.last_name,
        username=body.username,
        company_name=body.company_name,
        avatar_url=body.avatar,
    )
    await _audit(
        session,
        user,
        action="auth.signup_completed",
        summary=f"{user.email} completed their signup profile",
    )
    return SignedInUser(user=_summary(user))


class ForgotPasswordRequest(BaseModel):
    email: str


@router.post("/password/forgot", status_code=202, dependencies=[Depends(_mail_limit)])
async def forgot_password(session: SessionDep, body: ForgotPasswordRequest) -> dict[str, str]:
    """Mail a reset link. Always 202 — it must not reveal who has an account."""
    enforce_address_cooldown("reset-mail", body.email)
    await auth_service.request_password_reset(session, email=body.email)
    return {"status": "sent"}


class ResetPasswordRequest(BaseModel):
    token: str
    password: str

    @field_validator("password")
    @classmethod
    def _strong_enough(cls, v: str) -> str:
        if len(v) < auth_service.MIN_PASSWORD_LEN:
            raise ValueError(f"Use at least {auth_service.MIN_PASSWORD_LEN} characters")
        return v


@router.post("/password/reset", response_model=SignedInUser)
async def reset_password(
    session: SessionDep, response: Response, body: ResetPasswordRequest
) -> SignedInUser:
    """Consume a reset link, set the new password, and sign the user in."""
    user = await auth_service.reset_password(session, token=body.token, password=body.password)
    if user is None:
        raise HTTPException(status_code=400, detail="reset_link_invalid")
    await _audit(
        session, user, action="auth.password_reset", summary=f"{user.email} reset their password"
    )
    auth_service.set_session_cookie(response, auth_service.mint_session_for(user))
    return SignedInUser(user=_summary(user))


class OrgSummary(BaseModel):
    id: str
    name: str


class WorkspaceSummary(BaseModel):
    id: str
    organization_id: str
    name: str
    kind: WorkspaceKind


class MeResponse(BaseModel):
    user: UserSummary | None
    organization: OrgSummary | None
    organizations: list[OrgSummary]
    is_org_admin: bool
    # False while an OAuth signup is unfinished — the client routes to the completion form
    # instead of the app until it flips.
    profile_complete: bool
    current_workspace_id: str | None
    workspaces: list[WorkspaceSummary]
    labels: LabelPack


@router.get("/me", response_model=MeResponse)
async def me(ctx: SignupContextDep, session: SessionDep) -> MeResponse:
    user = await session.get(User, ctx.user_id)
    org = await session.get(Organization, ctx.org_id)
    workspaces = list(
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
    orgs = list(
        (
            await session.execute(
                select(Organization)
                .join(Membership, Membership.organization_id == Organization.id)
                .where(Membership.user_id == ctx.user_id)
                .order_by(Organization.created_at)
            )
        )
        .scalars()
        .all()
    )
    current = next((w for w in workspaces if w.id == ctx.current_workspace_id), None)
    return MeResponse(
        user=_summary(user) if user else None,
        organization=OrgSummary(id=org.id, name=org.name) if org else None,
        organizations=[OrgSummary(id=o.id, name=o.name) for o in orgs],
        is_org_admin=ctx.is_org_admin,
        profile_complete=ctx.profile_complete,
        current_workspace_id=ctx.current_workspace_id,
        workspaces=[
            WorkspaceSummary(id=w.id, organization_id=w.organization_id, name=w.name, kind=w.kind)
            for w in workspaces
        ],
        labels=await label_pack(session, workspace=current),
    )


class LogoutResponse(BaseModel):
    status: str


@router.post("/logout", response_model=LogoutResponse)
async def logout(response: Response) -> LogoutResponse:
    """Clear the session cookie. Nothing to bounce through — this used to hand back a WorkOS
    hosted-logout URL for the client to navigate to, but sign-out is purely local now."""
    auth_service.clear_session_cookie(response)
    return LogoutResponse(status="ok")
