"""Provider selection (per-workspace allow-list) + agent-path usage metering."""

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.sourcing import SourcingContext, sourcing_tools
from app.core.config import Settings
from app.core.policy import Policy
from app.ext.base import EmailVerdict, PersonHit, ProviderCapabilities, SearchPage
from app.ext.registry import _apply_selection, build_providers_for_org
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


def test_provider_selection_reads_the_policy_chain() -> None:
    def selection(*layers: dict[str, object]) -> list[str]:
        return Policy(layers=layers).get_str_list("providers")

    assert selection({"providers": ["pdl", "hunter"]}) == ["pdl", "hunter"]
    assert selection({"providers": []}) == []  # empty = use all
    assert selection({}) == []
    assert selection({"providers": "pdl"}) == []  # not a list → use all
    # the nearest layer defining the key wins
    assert selection({"providers": ["apollo"]}, {"providers": ["pdl"]}) == ["apollo"]


def test_apply_selection_filters_orders_and_falls_back() -> None:
    a, b, c = _Stub("pdl"), _Stub("apollo"), _Stub("hunter")
    providers = [a, b, c]
    assert _apply_selection(providers, None) == [a, b, c]  # None = all
    assert _apply_selection(providers, ["apollo", "pdl"]) == [b, a]  # filtered + reordered
    assert _apply_selection(providers, ["nope"]) == [a, b, c]  # no match → fall back to all


@pytest.mark.db
async def test_build_providers_respects_selection(db_session: AsyncSession) -> None:
    org = await make_org(db_session, slug="sel-build")
    # Hermetic: pin the platform keys so the developer's .env can't change the built set.
    keys = Settings(pdl_api_key="pk", apollo_api_key="ak", hunter_api_key="", unipile_api_key="")
    only_apollo = await build_providers_for_org(db_session, org.id, keys, selection=["apollo"])
    assert [p.key for p in only_apollo] == ["apollo"]
    # a selection matching nothing configured falls back to the full built set, never empty
    fallback = await build_providers_for_org(db_session, org.id, keys, selection=["nope"])
    assert [p.key for p in fallback] == ["pdl", "apollo"]


# --- agent-path metering -----------------------------------------------------


@pytest.mark.db
async def test_search_tool_meters_every_provider(db_session: AsyncSession) -> None:
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
        providers=[_Stub("pdl"), _Stub("apollo")],
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
    assert ("apollo", "search") in keys
