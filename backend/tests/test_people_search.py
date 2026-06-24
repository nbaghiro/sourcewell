"""People discovery: search scores/ranks hits; import normalizes them into Contacts + dedupes."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import seal, unseal
from app.models import (
    Organization,
    ProviderCredential,
    Workspace,
    WorkspaceKind,
)
from app.services.people import discovery, usage
from app.services.people.adapters.demo import DemoProvider
from app.services.people.adapters.registry import (
    PROVIDER_CATALOG,
    build_providers,
    build_providers_for_org,
)
from app.targeting import Targeting


async def test_demo_search_scores_and_ranks() -> None:
    query = Targeting(titles=["VP of Sales"], skills=["Salesforce", "Enterprise"], locations=["EU"])
    results = await discovery.search_people([DemoProvider()], query, limit=12)

    assert results, "demo provider should always return hits"
    assert all(r.provider == "demo" for r in results)
    # every hit was fit-scored, and the list is ranked best-first
    assert all(r.score > 0 for r in results)
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
    # dedupe across the result set
    keys = [discovery.dedupe_key(r) for r in results]
    assert len(keys) == len(set(keys))


async def test_demo_search_responds_to_every_filter_change() -> None:
    """Every criterion change — including the free-text keyword box — reshuffles results; a repeated
    query is stable. Guards the abstraction: a real provider's corpus reacts to every filter too."""

    async def names(*, keywords: str | None = None, titles: list[str] | None = None) -> list[str]:
        query = Targeting(keywords=keywords, titles=titles or [])
        res = await discovery.search_people([DemoProvider()], query, limit=8, use_cache=False)
        return [r.full_name for r in res]

    a = await names(keywords="fintech VPs")
    b = await names(keywords="healthcare nurses")
    assert a != b  # free-text keywords alone change the result set
    assert a == await names(keywords="fintech VPs")  # deterministic: same query -> same people
    # clearing a filter changes the results too
    assert await names(titles=["VP of Sales"]) != await names(titles=[])


def test_registry_falls_back_to_demo_without_keys() -> None:
    providers = build_providers()  # no pdl_api_key configured in tests
    assert any(p.key == "demo" for p in providers)


@pytest.mark.db
async def test_import_normalizes_and_dedupes(db_session: AsyncSession) -> None:
    org = Organization(name="Importer", slug="importer-co", plan="demo")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(organization_id=org.id, name="Pipeline", kind=WorkspaceKind.team)
    db_session.add(ws)
    await db_session.flush()

    hits = await discovery.search_people(
        [DemoProvider()], Targeting(titles=["VP of Sales"]), limit=6
    )
    created = await discovery.import_hits(db_session, workspace_id=ws.id, hits=hits)

    assert len(created) == len(hits)
    # normalized into the contacts table with provider provenance
    assert all(c.source == "demo" and c.workspace_id == ws.id for c in created)
    assert created[0].full_name and created[0].industry

    # re-importing the same hits is a no-op (deduped against existing contacts)
    again = await discovery.import_hits(db_session, workspace_id=ws.id, hits=hits)
    assert again == []


def test_secret_seal_roundtrips() -> None:
    assert unseal(seal("pdl-secret-key-123")) == "pdl-secret-key-123"


@pytest.mark.db
async def test_byo_credential_enables_real_provider(db_session: AsyncSession) -> None:
    org = Organization(name="BYO", slug="byo-co", plan="demo")
    db_session.add(org)
    await db_session.flush()

    # no credentials -> demo provider only
    before = await build_providers_for_org(db_session, org.id)
    assert [p.key for p in before] == ["demo"]

    # a BYO PDL key brings the real provider online (sealed at rest)
    db_session.add(
        ProviderCredential(
            organization_id=org.id, provider="pdl", secret=seal("test-key"), last4="-key"
        )
    )
    await db_session.flush()
    after = await build_providers_for_org(db_session, org.id)
    assert "pdl" in [p.key for p in after]


def test_catalog_includes_apollo_hunter_linkedin() -> None:
    keys = {s.key for s in PROVIDER_CATALOG}
    assert {"pdl", "apollo", "hunter", "linkedin"} <= keys


@pytest.mark.db
async def test_import_verifies_email_status(db_session: AsyncSession) -> None:
    org = Organization(name="V", slug="verify-co", plan="demo")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(organization_id=org.id, name="W", kind=WorkspaceKind.team)
    db_session.add(ws)
    await db_session.flush()

    providers = [DemoProvider()]
    hits = await discovery.search_people(providers, Targeting(titles=["VP of Sales"]), limit=4)
    await discovery.verify_hits(providers, hits)  # demo verifier marks well-formed emails valid
    assert all(h.email_status == "valid" for h in hits if h.email)

    created = await discovery.import_hits(db_session, workspace_id=ws.id, hits=hits)
    assert created and all(c.email_status == "valid" for c in created if c.email)


@pytest.mark.db
async def test_usage_record_increments(db_session: AsyncSession) -> None:
    org = Organization(name="U", slug="usage-co", plan="demo")
    db_session.add(org)
    await db_session.flush()
    await usage.record(db_session, organization_id=org.id, provider="demo", kind="search")
    await usage.record(db_session, organization_id=org.id, provider="demo", kind="search")
    rows = await usage.summary(db_session, org.id)
    assert rows and rows[0]["count"] == 2
