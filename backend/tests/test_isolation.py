"""Workspace-isolation tests at the context + service layer."""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.requests import Request
from starlette.responses import Response

from app.api.context import get_context
from app.models import MembershipRole
from app.services.workspace import tenancy as service
from tests import factories


def _req(
    user_id: str | None = None,
    workspace_id: str | None = None,
    organization_id: str | None = None,
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if user_id:
        headers.append((b"x-user-id", user_id.encode()))
    if workspace_id:
        headers.append((b"x-workspace-id", workspace_id.encode()))
    if organization_id:
        headers.append((b"x-organization-id", organization_id.encode()))
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
    user = await factories.make_user(db_session)
    await factories.make_membership(db_session, user=user, org=org, role=MembershipRole.member)
    await factories.make_space_grant(db_session, user=user, workspace=w1)

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
    user = await factories.make_user(db_session)
    await factories.make_membership(db_session, user=user, org=org, role=MembershipRole.member)
    await factories.make_space_grant(db_session, user=user, workspace=w1)

    with pytest.raises(HTTPException) as exc:
        await get_context(_req(user.id, foreign.id), Response(), db_session)
    assert exc.value.status_code == 403


@pytest.mark.db
async def test_workspace_grant_does_not_reach_a_sibling_workspace(
    db_session: AsyncSession,
) -> None:
    org = await factories.make_org(db_session, slug="sibling")
    granted = await factories.make_workspace(db_session, org=org, name="Granted")
    sibling = await factories.make_workspace(db_session, org=org, name="Sibling")
    user = await factories.make_workspace_member(db_session, org=org, workspace=granted)

    ctx = await get_context(_req(user.id, granted.id), Response(), db_session)
    assert ctx.allowed_workspace_ids == {granted.id}

    with pytest.raises(HTTPException) as exc:
        await get_context(_req(user.id, sibling.id), Response(), db_session)
    assert exc.value.status_code == 403


@pytest.mark.db
async def test_user_in_two_orgs_resolves_by_header_or_workspace(db_session: AsyncSession) -> None:
    org_a = await factories.make_org(db_session, slug="two-a")
    org_b = await factories.make_org(db_session, slug="two-b")
    ws_a = await factories.make_workspace(db_session, org=org_a, name="A")
    ws_b = await factories.make_workspace(db_session, org=org_b, name="B")
    user = await factories.make_user(db_session)
    await factories.make_membership(db_session, user=user, org=org_a, role=MembershipRole.org_admin)
    await factories.make_membership(db_session, user=user, org=org_b, role=MembershipRole.member)
    await factories.make_space_grant(db_session, user=user, workspace=ws_b)

    # Both orgs' workspaces are reachable: org_admin implies all of org A, org B by explicit grant.
    ctx = await get_context(_req(user.id, organization_id=org_a.id), Response(), db_session)
    assert ctx.allowed_workspace_ids == {ws_a.id, ws_b.id}
    assert ctx.org_id == org_a.id and ctx.is_org_admin

    # The workspace header wins over the org header, and carries its own org's roles.
    ctx = await get_context(_req(user.id, ws_b.id, org_a.id), Response(), db_session)
    assert ctx.org_id == org_b.id
    assert not ctx.is_org_admin

    # A fresh browser sends no selection at all: fall back to a membership rather than locking
    # the user out, so the app can load and let them switch.
    ctx = await get_context(_req(user.id), Response(), db_session)
    assert ctx.org_id in {org_a.id, org_b.id}

    # An organization the user does not belong to is refused outright.
    with pytest.raises(HTTPException) as exc:
        await get_context(_req(user.id, organization_id="not-a-member"), Response(), db_session)
    assert exc.value.status_code == 403


@pytest.mark.db
async def test_user_with_no_membership_is_rejected(db_session: AsyncSession) -> None:
    user = await factories.make_user(db_session)
    with pytest.raises(HTTPException) as exc:
        await get_context(_req(user.id), Response(), db_session)
    assert exc.value.status_code == 403
