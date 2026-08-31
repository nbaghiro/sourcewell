"""Per-user channel seats — the connected LinkedIn / email accounts behind Unipile.

A *seat* is a `Connection` row whose `external_id` is the Unipile `account_id`. The connect flow
(hosted auth, started from Settings by a signed-in user) upserts it; sourcing + messaging resolve
the `account_id` from it instead of a global setting, so every user operates on their own
connected account. Connecting a seat never creates a user — `provision_user` is reached only by
the OAuth sign-in path.
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.db import new_id
from app.core.logging import logger
from app.ext.unipile import unipile_connection
from app.models import (
    Connection,
    ConnectionProvider,
    ConnectionStatus,
    LoginAttempt,
    Membership,
    MembershipRole,
    SeatType,
    User,
    UserStatus,
)
from app.services.outreach.receiving import ensure_inbound_webhooks_quietly
from app.services.workspace import tenancy


def _opt(payload: object, key: str) -> str | None:
    if isinstance(payload, dict):
        value = payload.get(key)
        return value if isinstance(value, str) and value else None
    return None


def _flag(profile: object, key: str) -> bool:
    """A capability flag on the Unipile profile. Absent / null / false all mean "not on this seat";
    the paid tiers come back as `true` or as an object describing the subscription."""
    if not isinstance(profile, dict):
        return False
    value = profile.get(key)
    return bool(value) and value is not False


def seat_type_from_profile(profile: object) -> SeatType:
    """The account's real LinkedIn tier, read from `/users/me`.

    This used to be hardcoded to `recruiter` for every connected seat, which told every user their
    free account was a LinkedIn Recruiter one. The tier is not cosmetic: it decides whether the
    seat can send InMail at all, so an unknown or unreachable profile resolves to `basic` — the
    least-privileged reading — rather than claiming a capability the account may not have.
    """
    if _flag(profile, "recruiter"):
        return SeatType.recruiter
    if _flag(profile, "sales_navigator"):
        return SeatType.sales_nav
    if _flag(profile, "premium"):
        return SeatType.premium
    return SeatType.basic


async def upsert_seat(
    session: AsyncSession,
    *,
    organization_id: str,
    user_id: str,
    provider: ConnectionProvider,
    account_id: str,
    seat_type: SeatType = SeatType.basic,
    status: ConnectionStatus = ConnectionStatus.ok,
) -> Connection:
    """Create or refresh a user's seat for a provider (called on connect / reconnect)."""
    existing = (
        (
            await session.execute(
                select(Connection)
                .where(Connection.user_id == user_id, Connection.provider == provider)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        existing.external_id = account_id
        existing.seat_type = seat_type
        existing.status = status
        await session.flush()
        return existing
    seat = Connection(
        organization_id=organization_id,
        user_id=user_id,
        provider=provider,
        external_id=account_id,
        seat_type=seat_type,
        status=status,
    )
    session.add(seat)
    await session.flush()
    return seat


async def user_seat(
    session: AsyncSession, *, user_id: str, provider: ConnectionProvider
) -> Connection | None:
    """A user's healthy seat for a provider, or None if they haven't connected one."""
    return (
        (
            await session.execute(
                select(Connection)
                .where(
                    Connection.user_id == user_id,
                    Connection.provider == provider,
                    Connection.status == ConnectionStatus.ok,
                )
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def provision_user(
    session: AsyncSession, *, subject: str, name: str, email: str | None
) -> User:
    """Find or create a local user by federated identity `subject` (the WorkOS user id behind the
    Google / Microsoft buttons).

    A returning user is matched on `subject` and handed straight back — that match *is* "they
    signed up before". Otherwise this is a first sign-in: either they link to the org that invited
    them, or they get a fresh org + default workspace + org-admin membership and owe us the signup
    profile (`profile_completed_at is None`).
    """
    existing = (
        await session.execute(select(User).where(User.sso_subject == subject))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    now = datetime.now(UTC)
    # A teammate whose account already exists locally — an invited seat, or an address that signed
    # up with a password — links their OAuth identity to it rather than getting a second org of
    # their own. An address is global and unique, so there is exactly one row it can belong to.
    #
    # A disabled row is handed back untouched rather than linked or re-activated: `/auth/callback`
    # is what refuses to mint it a session, so that check lives there for every sign-in path.
    if email:
        local = (
            await session.execute(
                select(User).where(User.email == email, User.sso_subject.is_(None)).limit(1)
            )
        ).scalar_one_or_none()
        if local is not None:
            if local.status is UserStatus.disabled:
                return local
            local.sso_subject = subject
            local.status = UserStatus.active
            # The provider proved the address, whatever state the local row was left in.
            local.email_verified_at = local.email_verified_at or now
            # Joining an org that already exists: there is no company to name and no password to
            # set, so nothing is owed and they go straight into the app.
            local.profile_completed_at = local.profile_completed_at or now
            await session.flush()
            return local
    # A placeholder org named off the email domain. The provider gives us an address and a display
    # name and nothing else, so the real company name arrives with the completion form, which
    # renames this org (see `complete_signup_profile`).
    domain = email.split("@")[-1].split(".")[0] if email and "@" in email else "workspace"
    org = await tenancy.create_organization(
        session, name=domain.capitalize(), slug=f"{domain}-{new_id()[:8].lower()}"
    )
    user = User(
        email=email or f"{subject}@users.local",
        name=name or "User",
        sso_subject=subject,
        # The identity provider already proved this address — no confirmation email needed.
        email_verified_at=now,
        # ...but the signup profile is still outstanding: `profile_completed_at` stays null, which
        # is what routes them to the form instead of the dashboard.
    )
    session.add(user)
    await session.flush()
    session.add(Membership(user_id=user.id, organization_id=org.id, role=MembershipRole.org_admin))
    await session.flush()
    return user


async def home_org_id(session: AsyncSession, *, user_id: str) -> str | None:
    """The organization a user's seats belong to — their oldest membership."""
    return (
        (
            await session.execute(
                select(Membership.organization_id)
                .where(Membership.user_id == user_id)
                .order_by(Membership.created_at)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


# --- LinkedIn (Unipile hosted-auth) seat connect ------------------------------
#
# LinkedIn is not a way *in* to Sourcewell — you sign in with Google, Microsoft or a password, and
# then connect your LinkedIn account from Settings as a *sending seat*. That is the only thing the
# messaging layer ever needed from it, and it keeps one person's identity from being forked across
# a LinkedIn `member_urn` and an email address.


# Our provider → Unipile's own name for it in the hosted-auth wizard.
_UNIPILE_PROVIDERS = {
    ConnectionProvider.linkedin: "LINKEDIN",
    ConnectionProvider.gmail: "GOOGLE",
    ConnectionProvider.graph: "OUTLOOK",
}


async def start_seat_connect(
    session: AsyncSession, *, user_id: str, provider: ConnectionProvider
) -> str | None:
    """Wizard link for a signed-in user connecting a sending seat. None if unavailable.

    Mints no session and creates no user: the notify hop attaches the connected Unipile account to
    `user_id`, which is what the messaging layer resolves a sender from. The wizard is pinned to
    the one provider asked for, and that provider is recorded on the attempt — the notify hop
    carries only an account id, so this row is the only thing that knows a Gmail mailbox is coming
    back rather than a LinkedIn profile.

    Before this, the wizard was hardcoded to LinkedIn and there was no way to connect an email
    seat at all: `resolve_channel_seat` found nothing for email in every org, so per-recruiter
    mail fell back to a single global account or to dev SMTP.
    """
    s = get_settings()
    conn = unipile_connection()
    # Gate on the whole flow, not just the API client: starting a wizard whose notify hop is
    # disabled would strand the user on a wizard that can never report back.
    if conn is None or not s.seat_connect_enabled:
        return None
    await _purge_stale_attempts(session)
    state = new_id()
    session.add(LoginAttempt(state=state, status="pending", user_id=user_id, provider=provider))
    await session.flush()
    notify = f"{s.api_base_url}/settings/connections/notify?token={s.unipile_webhook_secret}"
    return await conn.create_link(
        user_ref=state,
        notify_url=notify,
        redirect_url=f"{s.frontend_url}/settings?connected={provider.value}",
        providers=[_UNIPILE_PROVIDERS[provider]],
    )


async def _purge_stale_attempts(session: AsyncSession) -> None:
    """Drop abandoned connect attempts. Nobody finishes a wizard an hour later, and the rows
    would otherwise accumulate forever."""
    cutoff = datetime.now(UTC) - timedelta(minutes=get_settings().login_attempt_ttl_minutes)
    await session.execute(delete(LoginAttempt).where(LoginAttempt.created_at < cutoff))


async def complete_seat_connect(session: AsyncSession, *, state: str, account_id: str) -> None:
    """Unipile notify: bind the connected account to the user whose wizard this was.

    The provider comes off the attempt, not the payload — the wizard was pinned to it, so it is
    what actually got connected. Only LinkedIn has a tier to read; a mailbox is simply `email`.
    """
    attempt = (
        await session.execute(select(LoginAttempt).where(LoginAttempt.state == state))
    ).scalar_one_or_none()
    if attempt is None or attempt.user_id is None:
        return
    user = await session.get(User, attempt.user_id)
    if user is None:
        return
    org_id = await home_org_id(session, user_id=user.id)
    if org_id is None:
        return
    if attempt.provider is ConnectionProvider.linkedin:
        conn = unipile_connection()
        profile = await conn.profile(account_id=account_id) if conn is not None else None
        seat_type = seat_type_from_profile(profile)
    else:
        # A mailbox has no LinkedIn tier to read: it can send, or it isn't connected.
        seat_type = SeatType.email
    await upsert_seat(
        session,
        organization_id=org_id,
        user_id=user.id,
        provider=attempt.provider,
        account_id=account_id,
        seat_type=seat_type,
    )
    attempt.account_id = account_id
    attempt.status = "ready"
    await session.flush()
    # A brand-new deployment can take its first seat before it has ever booted with Unipile
    # configured; re-assert the subscription here so that seat's replies are heard from the start.
    await ensure_inbound_webhooks_quietly()


async def forget_account(account_id: str) -> None:
    """Tell Unipile to drop a connected account. Fail-soft: the local seat goes regardless."""
    conn = unipile_connection()
    if conn is None:
        return
    if not await conn.delete_account(account_id=account_id):
        logger.warning(
            "unipile: could not delete account %s — it stays connected (and billed) there",
            account_id,
        )
