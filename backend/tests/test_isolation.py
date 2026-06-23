"""Workspace-isolation tests at the context + service layer."""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from app.models import MembershipRole, MembershipScope
from app.workspace import tenancy as service
from app.workspace.tenancy import get_context
from tests import factories


def _req(user_id: str | None = None, workspace_id: str | None = None) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if user_id:
        headers.append((b"x-user-id", user_id.encode()))
    if workspace_id:
        headers.append((b"x-workspace-id", workspace_id.encode()))
    return Request({"type": "http", "headers": headers})


@pytest.mark.db
async def test_org_admin_sees_all_org_workspaces_only(db_session: AsyncSession) -> None:
    org_a = await factories.make_org(db_session, slug="a")
    org_b = await factories.make_org(db_session, slug="b")
    a1 = await factories.make_workspace(db_session, org=org_a, name="A1")
    a2 = await factories.make_workspace(db_session, org=org_a, name="A2")
    b1 = await factories.make_workspace(db_session, org=org_b, name="B1")
    admin = await factories.make_org_admin(db_session, org=org_a)

    ctx = await get_context(_req(admin.id), Response(), db_session)
    assert ctx.is_org_admin
    assert ctx.allowed_workspace_ids == {a1.id, a2.id}
    assert b1.id not in ctx.allowed_workspace_ids


@pytest.mark.db
async def test_workspace_member_sees_only_assigned(db_session: AsyncSession) -> None:
    org = await factories.make_org(db_session, slug="org")
    w1 = await factories.make_workspace(db_session, org=org, name="W1")
    w2 = await factories.make_workspace(db_session, org=org, name="W2")
    user = await factories.make_user(db_session, org=org)
    await factories.make_membership(
        db_session,
        user=user,
        org=org,
        scope=MembershipScope.workspace,
        role=MembershipRole.member,
        workspace=w1,
    )

    ctx = await get_context(_req(user.id), Response(), db_session)
    assert not ctx.is_org_admin
    assert ctx.allowed_workspace_ids == {w1.id}
    assert w2.id not in ctx.allowed_workspace_ids

    visible = await service.list_workspaces(
        db_session, org_id=org.id, allowed_ids=ctx.allowed_workspace_ids
    )
    assert {w.id for w in visible} == {w1.id}


@pytest.mark.db
async def test_foreign_workspace_header_is_rejected(db_session: AsyncSession) -> None:
    org = await factories.make_org(db_session, slug="org")
    w1 = await factories.make_workspace(db_session, org=org, name="W1")
    other = await factories.make_org(db_session, slug="other")
    foreign = await factories.make_workspace(db_session, org=other, name="OW")
    user = await factories.make_user(db_session, org=org)
    await factories.make_membership(
        db_session,
        user=user,
        org=org,
        scope=MembershipScope.workspace,
        role=MembershipRole.member,
        workspace=w1,
    )

    with pytest.raises(HTTPException) as exc:
        await get_context(_req(user.id, foreign.id), Response(), db_session)
    assert exc.value.status_code == 403
