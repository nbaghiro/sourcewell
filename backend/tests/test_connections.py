"""Per-user channel seats — the Connection-based account resolver."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ext.unipile import UnipileProvider
from app.models import (
    ConnectionProvider,
    ConnectionStatus,
    MembershipRole,
    User,
    UserStatus,
)
from app.services.workspace.connections import (
    home_org_id,
    provision_user,
    upsert_seat,
    user_seat,
)
from tests.factories import make_membership, make_org, make_user

_LINKEDIN = ConnectionProvider.linkedin


@pytest.mark.db
async def test_upsert_seat_creates_then_updates(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="cx-upsert")
    user = await make_user(db_session)
    seat = await upsert_seat(
        db_session, organization_id=org.id, user_id=user.id, provider=_LINKEDIN, account_id="acct-1"
    )
    assert seat.external_id == "acct-1"
    again = await upsert_seat(
        db_session, organization_id=org.id, user_id=user.id, provider=_LINKEDIN, account_id="acct-2"
    )
    assert again.id == seat.id  # updated, not duplicated
    assert again.external_id == "acct-2"


@pytest.mark.db
async def test_user_seat_resolves_healthy_only(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="cx-resolve")
    user = await make_user(db_session)
    assert await user_seat(db_session, user_id=user.id, provider=_LINKEDIN) is None

    await upsert_seat(
        db_session, organization_id=org.id, user_id=user.id, provider=_LINKEDIN, account_id="acct-x"
    )
    seat = await user_seat(db_session, user_id=user.id, provider=_LINKEDIN)
    assert seat is not None and seat.external_id == "acct-x"

    # a needs-reauth seat no longer resolves
    await upsert_seat(
        db_session,
        organization_id=org.id,
        user_id=user.id,
        provider=_LINKEDIN,
        account_id="acct-x",
        status=ConnectionStatus.needs_reauth,
    )
    assert await user_seat(db_session, user_id=user.id, provider=_LINKEDIN) is None


@pytest.mark.db
async def test_provision_user_links_invited_member_by_email(db_session: AsyncSession) -> None:
    """An invited teammate (sso_subject=None) signing in via SSO links to their org by email —
    not a brand-new org of their own (the split-billing / split-team regression)."""
    org = await make_org(db_session, slug="cx-invite")
    invited = User(
        email="invitee@co.com", name="Invitee", sso_subject=None, status=UserStatus.invited
    )
    db_session.add(invited)
    await db_session.flush()
    await make_membership(db_session, user=invited, org=org, role=MembershipRole.member)

    user = await provision_user(
        db_session, subject="wos-new", name="Invitee", email="invitee@co.com"
    )

    assert user.id == invited.id  # linked to the existing user, NOT provisioned a new org
    assert await home_org_id(db_session, user_id=user.id) == org.id
    assert user.sso_subject == "wos-new"
    assert user.status == UserStatus.active


def test_unipile_provider_uses_passed_account_id() -> None:
    assert UnipileProvider("key", account_id="acct-seat")._account == "acct-seat"
    assert UnipileProvider("key")._account == ""  # settings fallback (unset in tests)
