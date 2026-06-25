"""Provider selection (per-workspace allow-list) + agent-path usage metering."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sourcing import SourcingContext, sourcing_tools
from app.ext.base import EmailVerdict, PersonHit, ProviderCapabilities, SearchPage
from app.ext.registry import _apply_selection, build_providers_for_org, provider_selection
from app.models import Campaign, ProviderUsage
from app.targeting import Targeting
from tests.factories import make_org, make_workspace


class _Stub:
    """A minimal SourceProvider for selection/metering tests (no HTTP)."""

    def __init__(self, key: str) -> None:
        self.key = key
        self.name = key
        self.capabilities = ProviderCapabilities(search=True, enrich=False, verify_email=False)

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage:
        return SearchPage(hits=[], total=0)

    async def enrich(
        self,
        *,
        email: str | None = None,
        linkedin_url: str | None = None,
        name: str | None = None,
        company: str | None = None,
    ) -> PersonHit | None:
        return None

    async def verify_email(self, email: str) -> EmailVerdict:
        return EmailVerdict(email=email, status="unknown")

    async def verify_credentials(self) -> bool:
        return True


# --- selection helpers (units) -----------------------------------------------


def test_provider_selection_reads_settings() -> None:
    assert provider_selection({"providers": ["pdl", "hunter"]}) == ["pdl", "hunter"]
    assert provider_selection({"providers": []}) is None  # empty = use all
    assert provider_selection({}) is None
    assert provider_selection({"providers": "pdl"}) is None  # not a list → use all


def test_apply_selection_filters_orders_and_falls_back() -> None:
    a, b, c = _Stub("pdl"), _Stub("apollo"), _Stub("demo")
    providers = [a, b, c]
    assert _apply_selection(providers, None) == [a, b, c]  # None = all
    assert _apply_selection(providers, ["apollo", "pdl"]) == [b, a]  # filtered + reordered
    assert _apply_selection(providers, ["nope"]) == [a, b, c]  # no match → fall back to all


@pytest.mark.db
async def test_build_providers_respects_selection(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="sel-build")
    only_demo = await build_providers_for_org(db_session, org.id, selection=["demo"])
    assert [p.key for p in only_demo] == ["demo"]
    # a selection matching nothing configured falls back to the full (demo) set, never empty
    fallback = await build_providers_for_org(db_session, org.id, selection=["pdl"])
    assert fallback


# --- agent-path metering -----------------------------------------------------


@pytest.mark.db
async def test_search_tool_meters_real_providers(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="sel-meter")
    ws = await make_workspace(db_session, org=org)
    c = Campaign(workspace_id=ws.id, name="C", criteria={}, sequence=[])
    db_session.add(c)
    await db_session.flush()
    ctx = SourcingContext(
        session=db_session,
        workspace_id=ws.id,
        organization_id=org.id,
        campaign=c,
        providers=[_Stub("pdl"), _Stub("demo")],
        targeting=Targeting(),
    )
    tools = {t.name: t for t in sourcing_tools(ctx)}
    await tools["search"].run({"limit": 5})

    rows = (
        (
            await db_session.execute(
                select(ProviderUsage).where(ProviderUsage.organization_id == org.id)
            )
        )
        .scalars()
        .all()
    )
    keys = {(r.provider, r.kind) for r in rows}
    assert ("pdl", "search") in keys
    assert ("demo", "search") not in keys  # the synthetic provider is not metered
