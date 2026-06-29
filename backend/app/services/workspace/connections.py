"""Per-user channel seats — the connected LinkedIn / email accounts behind Unipile.

A *seat* is a `Connection` row whose `external_id` is the Unipile `account_id`. The connect flow
(hosted auth) upserts it; sourcing + messaging resolve the `account_id` from it instead of a global
setting, so every user operates on their own connected account. The unblocker for the whole track.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import new_id
from app.models import (
    Connection,
    ConnectionProvider,
    ConnectionStatus,
    Membership,
    MembershipRole,
    MembershipScope,
    Organization,
    SeatType,
    User,
    UserStatus,
    Workspace,
    WorkspaceKind,
)


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


async def seat_account_id(
    session: AsyncSession, *, user_id: str, provider: ConnectionProvider
) -> str | None:
    """The Unipile account id for a user's healthy seat, or None if not connected."""
    return (
        (
            await session.execute(
                select(Connection.external_id)
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


async def workspace_seat_account_id(
    session: AsyncSession, *, workspace_id: str, provider: ConnectionProvider
) -> str | None:
    """A healthy seat `account_id` for *some* member of the workspace (the default sender).

    The per-campaign sender model (a designated owner) lands with the channel-send phase; until then
    any connected member's seat serves the workspace.
    """
    return (
        (
            await session.execute(
                select(Connection.external_id)
                .join(Membership, Membership.user_id == Connection.user_id)
                .where(
                    Membership.workspace_id == workspace_id,
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
    """Find or create a local user by federated identity `subject` (a WorkOS user id or a LinkedIn
    `member_urn`). First sign-in provisions an org + default workspace + org-admin membership.
    """
    existing = (
        await session.execute(select(User).where(User.sso_subject == subject))
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    # An invited teammate signing in via SSO for the first time has no `sso_subject` yet — link them
    # to the org they were invited into (matched by email), not a brand-new org of their own.
    if email:
        invited = (
            await session.execute(
                select(User).where(User.email == email, User.sso_subject.is_(None)).limit(1)
            )
        ).scalar_one_or_none()
        if invited is not None:
            invited.sso_subject = subject
            invited.status = UserStatus.active
            await session.flush()
            return invited
    domain = email.split("@")[-1].split(".")[0] if email and "@" in email else "workspace"
    org = Organization(name=domain.capitalize(), slug=f"{domain}-{new_id()[:8].lower()}")
    session.add(org)
    await session.flush()
    session.add(
        Workspace(organization_id=org.id, name="Default workspace", kind=WorkspaceKind.team)
    )
    await session.flush()
    user = User(
        organization_id=org.id,
        email=email or f"{subject}@users.local",
        name=name or "User",
        sso_subject=subject,
    )
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


async def provision_from_linkedin(
    session: AsyncSession,
    *,
    member_urn: str,
    name: str,
    email: str | None,
    account_id: str,
    seat_type: SeatType = SeatType.basic,
) -> User:
    """Provision the local user for a LinkedIn member (keyed on `member_urn`) and (re)connect the
    Unipile seat behind their Connection.
    """
    user = await provision_user(session, subject=member_urn, name=name, email=email)
    await upsert_seat(
        session,
        organization_id=user.organization_id,
        user_id=user.id,
        provider=ConnectionProvider.linkedin,
        account_id=account_id,
        seat_type=seat_type,
    )
    return user
