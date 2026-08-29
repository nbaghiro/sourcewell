"""Self-serve plan change (the non-Stripe upgrade/downgrade path)."""

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.billing import PlanIn, change_plan
from app.api.context import TenantContext
from app.models import MembershipRole
from tests.factories import make_org


def _ctx(org_id: str, *, is_admin: bool = True) -> TenantContext:
    return TenantContext(
        user_id="u1",
        org_id=org_id,
        roles=frozenset({MembershipRole.org_admin} if is_admin else set()),
        is_org_admin=is_admin,
        allowed_workspace_ids=frozenset(),
        current_workspace_id=None,
    )


@pytest.mark.db
async def test_change_plan_upgrades_then_downgrades(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="planco")  # defaults to the free plan
    ctx = _ctx(org.id)

    up = await change_plan(PlanIn(plan="pro"), ctx, db_session)
    assert (up.plan, up.allowance) == ("pro", 5_000)
    assert org.plan == "pro"

    up = await change_plan(PlanIn(plan="premium"), ctx, db_session)
    assert (up.plan, up.allowance) == ("premium", 25_000)

    down = await change_plan(PlanIn(plan="free"), ctx, db_session)
    assert (down.plan, down.allowance) == ("free", 200)
    assert org.plan == "free"


@pytest.mark.db
async def test_change_plan_rejects_unknown_plan(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="planco2")
    with pytest.raises(HTTPException) as exc:
        await change_plan(PlanIn(plan="enterprise"), _ctx(org.id), db_session)
    assert exc.value.status_code == 400


@pytest.mark.db
async def test_change_plan_requires_org_admin(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="planco3")
    with pytest.raises(HTTPException) as exc:
        await change_plan(PlanIn(plan="pro"), _ctx(org.id, is_admin=False), db_session)
    assert exc.value.status_code == 403
