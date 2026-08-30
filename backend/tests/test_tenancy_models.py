import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Connection,
    ConnectionProvider,
    Membership,
    MembershipRole,
    Organization,
    SeatType,
    SpaceGrant,
    SpaceRole,
    User,
    Workspace,
    WorkspaceKind,
)


@pytest.mark.db
async def test_tenancy_round_trip(db_session: AsyncSession) -> None:
    org = Organization(name="Acme Agency", slug="acme")
    db_session.add(org)
    await db_session.flush()

    ws = Workspace(organization_id=org.id, name="Client A", kind=WorkspaceKind.client)
    db_session.add(ws)
    await db_session.flush()

    user = User(email="r@acme.com", name="Recruiter")
    db_session.add(user)
    await db_session.flush()

    member = Membership(user_id=user.id, organization_id=org.id, role=MembershipRole.member)
    db_session.add(member)
    await db_session.flush()

    grant = SpaceGrant(user_id=user.id, workspace_id=ws.id, role=SpaceRole.member)
    db_session.add(grant)
    await db_session.flush()

    conn = Connection(
        organization_id=org.id,
        user_id=user.id,
        provider=ConnectionProvider.gmail,
        seat_type=SeatType.email,
    )
    db_session.add(conn)
    await db_session.flush()

    assert len(org.id) == 26  # ULID
    assert ws.organization_id == org.id
    assert member.organization_id == org.id
    assert member.role == MembershipRole.member
    assert grant.workspace_id == ws.id
    assert grant.role == SpaceRole.member
    assert conn.provider == ConnectionProvider.gmail
    assert conn.daily_sent == 0
