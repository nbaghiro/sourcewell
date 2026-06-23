import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Connection,
    ConnectionProvider,
    Membership,
    MembershipRole,
    MembershipScope,
    Organization,
    SeatType,
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

    user = User(organization_id=org.id, email="r@acme.com", name="Recruiter")
    db_session.add(user)
    await db_session.flush()

    member = Membership(
        user_id=user.id,
        organization_id=org.id,
        scope=MembershipScope.workspace,
        workspace_id=ws.id,
        role=MembershipRole.member,
    )
    db_session.add(member)
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
    assert user.organization_id == org.id
    assert member.scope == MembershipScope.workspace
    assert member.workspace_id == ws.id
    assert member.role == MembershipRole.member
    assert conn.provider == ConnectionProvider.gmail
    assert conn.daily_sent == 0
