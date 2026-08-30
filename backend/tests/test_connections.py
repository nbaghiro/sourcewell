"""Per-user channel seats — the Connection-based account resolver."""

from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.ext.unipile import UnipileProvider
from app.models import (
    Campaign,
    Channel,
    ConnectionProvider,
    ConnectionStatus,
    MembershipRole,
    User,
    UserStatus,
)
from app.services.outreach.messaging import linkedin_transport_ready, resolve_channel_seat
from app.services.workspace.connections import (
    home_org_id,
    provision_user,
    upsert_seat,
    user_seat,
)
from tests.factories import (
    make_membership,
    make_org,
    make_user,
    make_workspace,
    make_workspace_member,
)

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
async def test_provision_user_links_a_teammate_who_accepted_their_invite(
    db_session: AsyncSession,
) -> None:
    """An invite that was accepted (the emailed link was clicked, so the address is proven) links
    the OAuth identity to that account — not a brand-new org of their own, which was the
    split-billing / split-team regression."""
    org = await make_org(db_session, slug="cx-invite")
    invited = User(
        email="invitee@co.com",
        name="Invitee",
        sso_subject=None,
        status=UserStatus.invited,
        email_verified_at=datetime.now(UTC),  # they clicked the invitation link
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


# --- the connect surface: no seat may claim to be live without an account -------


@pytest.mark.db
async def test_a_seat_without_an_account_is_not_reported_as_linked(
    db_client: AsyncClient, db_session: AsyncSession
) -> None:
    """What made Settings claim LinkedIn was connected: a seat row with no Unipile account."""
    org = await make_org(db_session, slug="cx-linked")
    user = await make_user(db_session, org=org, role=MembershipRole.org_admin)
    await upsert_seat(
        db_session, organization_id=org.id, user_id=user.id, provider=_LINKEDIN, account_id=""
    )
    rows = (await db_client.get("/settings/connections", headers={"X-User-Id": user.id})).json()
    assert [(c["provider"], c["linked"]) for c in rows] == [("linkedin", False)]

    # ...and once the wizard's notify binds a real account, it flips
    await upsert_seat(
        db_session,
        organization_id=org.id,
        user_id=user.id,
        provider=_LINKEDIN,
        account_id="acct-real",
    )
    rows = (await db_client.get("/settings/connections", headers={"X-User-Id": user.id})).json()
    assert [(c["provider"], c["linked"]) for c in rows] == [("linkedin", True)]


@pytest.mark.db
async def test_an_unlinked_seat_cannot_be_sent_from(
    db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The send path already ignores such a seat — this pins that the two agree."""
    org = await make_org(db_session, slug="cx-nosend")
    ws = await make_workspace(db_session, org=org)
    user = await make_workspace_member(db_session, org=org, workspace=ws)
    campaign = Campaign(workspace_id=ws.id, name="C", sequence=[], created_by_user_id=user.id)
    db_session.add(campaign)
    await db_session.flush()
    await upsert_seat(
        db_session, organization_id=org.id, user_id=user.id, provider=_LINKEDIN, account_id=""
    )
    # Unipile is configured, so the only thing standing between this seat and a send is its own
    # missing account id — which is exactly what "linked" means in Settings.
    monkeypatch.setattr(
        "app.ext.unipile.get_settings",
        lambda: Settings(unipile_api_key="key", unipile_dsn="https://api9.unipile.com:9999"),
    )
    monkeypatch.setattr(
        "app.services.outreach.messaging.get_settings",
        lambda: Settings(unipile_api_key="key", unipile_dsn="https://api9.unipile.com:9999"),
    )
    seat = await resolve_channel_seat(db_session, campaign=campaign, channel=Channel.linkedin)
    assert seat is not None and not seat.external_id
    assert not linkedin_transport_ready(seat)
