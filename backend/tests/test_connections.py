"""Provider track · Phase 1: per-user channel seats — the Connection-based account resolver."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.ext.unipile import UnipileProvider
from app.models import (
    ConnectionProvider,
    ConnectionStatus,
    MembershipRole,
    MembershipScope,
)
from app.services.workspace.connections import (
    seat_account_id,
    upsert_seat,
    workspace_seat_account_id,
)
from tests.factories import make_membership, make_org, make_user, make_workspace

_LINKEDIN = ConnectionProvider.linkedin


@pytest.mark.db
async def test_upsert_seat_creates_then_updates(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="cx-upsert")
    user = await make_user(db_session, org=org)
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
async def test_seat_account_id_resolves_healthy_only(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="cx-resolve")
    user = await make_user(db_session, org=org)
    assert await seat_account_id(db_session, user_id=user.id, provider=_LINKEDIN) is None

    await upsert_seat(
        db_session, organization_id=org.id, user_id=user.id, provider=_LINKEDIN, account_id="acct-x"
    )
    assert await seat_account_id(db_session, user_id=user.id, provider=_LINKEDIN) == "acct-x"

    # a needs-reauth seat no longer resolves
    await upsert_seat(
        db_session,
        organization_id=org.id,
        user_id=user.id,
        provider=_LINKEDIN,
        account_id="acct-x",
        status=ConnectionStatus.needs_reauth,
    )
    assert await seat_account_id(db_session, user_id=user.id, provider=_LINKEDIN) is None


@pytest.mark.db
async def test_workspace_seat_resolves_via_membership(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="cx-ws")
    ws = await make_workspace(db_session, org=org)
    user = await make_user(db_session, org=org)
    await make_membership(
        db_session,
        user=user,
        org=org,
        scope=MembershipScope.workspace,
        role=MembershipRole.member,
        workspace=ws,
    )
    await upsert_seat(
        db_session,
        organization_id=org.id,
        user_id=user.id,
        provider=_LINKEDIN,
        account_id="acct-ws",
    )
    found = await workspace_seat_account_id(db_session, workspace_id=ws.id, provider=_LINKEDIN)
    assert found == "acct-ws"


def test_unipile_provider_uses_passed_account_id() -> None:
    assert UnipileProvider("key", account_id="acct-seat")._account == "acct-seat"
    assert UnipileProvider("key")._account == ""  # settings fallback (unset in tests)
