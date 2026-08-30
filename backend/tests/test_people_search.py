"""People discovery: search scores/ranks hits; import normalizes them into Contacts + dedupes."""

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.crypto import seal, unseal
from app.ext.apollo import ApolloProvider
from app.ext.base import (
    EmailVerdict,
    PersonHit,
    ProviderCapabilities,
    ProviderError,
    SearchPage,
)
from app.ext.pdl import PDLProvider
from app.ext.registry import (
    PROVIDER_CATALOG,
    build_providers,
    build_providers_for_org,
)
from app.ext.unipile import UnipileProvider
from app.models import (
    Organization,
    ProviderCredential,
    Workspace,
    WorkspaceKind,
)
from app.services.sourcing import discovery, usage
from app.targeting import Targeting
from tests.fakes import FakeSourceProvider, fake_roster, make_hit


async def test_search_scores_ranks_and_dedupes() -> None:
    query = Targeting(titles=["VP of Sales"], skills=["Salesforce", "Enterprise"], locations=["EU"])
    # the second provider re-serves one of the first provider's people (same email) plus one new one
    overlap = [
        fake_roster()[0].model_copy(update={"provider": "other"}),
        make_hit(9, "Mia Foster", "VP of Sales", "Quill", "EU", ["Enterprise"], provider="other"),
    ]
    providers = [FakeSourceProvider(), FakeSourceProvider(overlap, key="other")]
    results = (await discovery.search_people(providers, query, limit=12, use_cache=False)).hits

    # deduped across providers: 8 from the first + 1 genuinely new from the second
    assert len(results) == 9
    keys = [discovery.dedupe_key(r) for r in results]
    assert len(keys) == len(set(keys))
    # every hit was fit-scored, and the list is ranked best-first
    assert [r.score for r in results] == sorted((r.score for r in results), reverse=True)
    assert results[0].title == "VP of Sales" and results[0].score > 0


def test_registry_builds_nothing_without_keys() -> None:
    # Hermetic: ignore any platform keys in the developer's .env.
    no_keys = Settings(pdl_api_key="", apollo_api_key="", hunter_api_key="", unipile_api_key="")
    assert list(build_providers(no_keys)) == []


@pytest.mark.db
async def test_import_normalizes_and_dedupes(db_session: AsyncSession) -> None:
    org = Organization(name="Importer", slug="importer-co", plan="demo")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(organization_id=org.id, name="Pipeline", kind=WorkspaceKind.team)
    db_session.add(ws)
    await db_session.flush()

    hits = (
        await discovery.search_people(
            [FakeSourceProvider()], Targeting(titles=["VP of Sales"]), limit=6, use_cache=False
        )
    ).hits
    created = await discovery.import_hits(db_session, workspace_id=ws.id, hits=hits)

    assert len(created) == len(hits)
    # normalized into the contacts table with provider provenance
    assert all(c.source == "fake" and c.workspace_id == ws.id for c in created)
    assert created[0].full_name and created[0].industry

    # re-importing the same hits is a no-op (deduped against existing contacts)
    again = await discovery.import_hits(db_session, workspace_id=ws.id, hits=hits)
    assert again == []


def test_providers_capture_seniority_function_technologies() -> None:
    # The provider search DSL uses seniority/function/technologies; these must survive onto the hit
    # (they were being dropped), so downstream scoring can eventually use them.
    pdl = PDLProvider("k")._normalize(
        {
            "full_name": "Ada Lovelace",
            "job_title": "Staff Engineer",
            "job_title_levels": ["senior"],
            "job_title_role": "engineering",
        }
    )
    assert pdl.seniority == "senior" and pdl.function == "engineering"

    apollo = ApolloProvider("k")._normalize(
        {
            "name": "Grace Hopper",
            "title": "VP Engineering",
            "seniority": "vp",
            "departments": ["engineering"],
            "organization": {"technology_names": ["Kafka", "Go"]},
        }
    )
    assert apollo.seniority == "vp" and apollo.function == "engineering"
    assert apollo.technologies == ["Kafka", "Go"]


@pytest.mark.db
async def test_import_persists_captured_attributes(db_session: AsyncSession) -> None:
    org = Organization(name="Attr", slug="attr-co", plan="demo")
    db_session.add(org)
    await db_session.flush()
    ws = Workspace(organization_id=org.id, name="W", kind=WorkspaceKind.team)
    db_session.add(ws)
    await db_session.flush()

    hit = PersonHit(
        provider="pdl",
        full_name="Ada Lovelace",
        title="Staff Engineer",
        email="ada@example.com",
        seniority="senior",
        function="engineering",
        technologies=["Kafka", "Go"],
    )
    created = await discovery.import_hits(db_session, workspace_id=ws.id, hits=[hit])
    assert len(created) == 1
    attrs = created[0].attributes
    assert attrs["seniority"] == "senior"
    assert attrs["function"] == "engineering"
    assert attrs["technologies"] == ["Kafka", "Go"]


def test_secret_seal_roundtrips() -> None:
    assert unseal(seal("pdl-secret-key-123")) == "pdl-secret-key-123"


@pytest.mark.db
async def test_byo_credential_enables_real_provider(db_session: AsyncSession) -> None:
    org = Organization(name="BYO", slug="byo-co", plan="demo")
    db_session.add(org)
    await db_session.flush()

    # Hermetic: ignore any platform keys in the developer's .env (e.g. a real Unipile key).
    no_platform_keys = Settings(
        pdl_api_key="", apollo_api_key="", hunter_api_key="", unipile_api_key=""
    )

    # no credentials and no platform keys -> no providers
    before = await build_providers_for_org(db_session, org.id, no_platform_keys)
    assert list(before) == []

    # a BYO PDL key brings the real provider online (sealed at rest)
    db_session.add(
        ProviderCredential(
            organization_id=org.id, provider="pdl", secret=seal("test-key"), last4="-key"
        )
    )
    await db_session.flush()
    after = await build_providers_for_org(db_session, org.id, no_platform_keys)
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

    providers = [FakeSourceProvider()]
    hits = (
        await discovery.search_people(
            providers, Targeting(titles=["VP of Sales"]), limit=4, use_cache=False
        )
    ).hits
    await discovery.verify_hits(providers, hits)  # the verifier marks well-formed emails valid
    assert all(h.email_status == "valid" for h in hits if h.email)

    created = await discovery.import_hits(db_session, workspace_id=ws.id, hits=hits)
    assert created and all(c.email_status == "valid" for c in created if c.email)


@pytest.mark.db
async def test_usage_record_increments(db_session: AsyncSession) -> None:
    org = Organization(name="U", slug="usage-co", plan="demo")
    db_session.add(org)
    await db_session.flush()
    await usage.record(db_session, organization_id=org.id, provider="pdl", kind="search")
    await usage.record(db_session, organization_id=org.id, provider="pdl", kind="search")
    rows = await usage.summary(db_session, org.id)
    assert rows and rows[0]["count"] == 2


# --- failures are reported, not counted as zero results ----------------------


class _Broken:
    """A provider whose upstream is refusing it (revoked key, rate limit, no seat)."""

    key = "linkedin"
    name = "Broken"
    capabilities = ProviderCapabilities(search=True)

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage:
        raise ProviderError(self.key, "HTTP 401 — Missing credentials", status=401)

    async def enrich(self, **kwargs: object) -> None:
        return None

    async def verify_email(self, email: str) -> EmailVerdict:
        return EmailVerdict(email=email)

    async def verify_credentials(self) -> bool:
        return False


async def test_a_failing_provider_is_reported_not_silently_empty() -> None:
    """The bug this replaces: a 4xx became an empty page, so a revoked key looked exactly like
    'nobody matched'. Empty results with no failures must mean something different from empty
    results with one."""
    outcome = await discovery.search_people(
        [_Broken()], Targeting(titles=["VP of Sales"]), use_cache=False
    )
    assert outcome.hits == []
    assert [(f.provider, f.message) for f in outcome.failures] == [
        ("linkedin", "HTTP 401 — Missing credentials")
    ]


async def test_a_genuinely_empty_search_reports_no_failures() -> None:
    """The other half of the contract — otherwise the client can't tell the two apart."""
    outcome = await discovery.search_people(
        [FakeSourceProvider()], Targeting(titles=["VP of Sales"]), use_cache=False
    )
    assert outcome.hits and outcome.failures == []


async def test_one_broken_provider_does_not_sink_the_others() -> None:
    """Fan-out degrades to the providers that still work rather than failing the whole search."""
    outcome = await discovery.search_people(
        [_Broken(), FakeSourceProvider()], Targeting(titles=["VP of Sales"]), use_cache=False
    )
    assert outcome.hits, "the healthy provider's results still come back"
    assert [f.provider for f in outcome.failures] == ["linkedin"]


async def test_a_short_search_is_not_cached() -> None:
    """Caching a run that lost a provider would keep serving the gap for the whole TTL."""
    targeting = Targeting(titles=["Cache", "Guard"], keywords="not-cached")
    first = await discovery.search_people([_Broken(), FakeSourceProvider()], targeting)
    assert first.failures
    second = await discovery.search_people([FakeSourceProvider()], targeting)
    assert second.failures == []


async def test_linkedin_search_without_a_seat_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exact state that looked like 'no results': Unipile configured, no account connected."""
    configured = Settings(unipile_api_key="key", unipile_dsn="https://api9.unipile.com:9999")
    monkeypatch.setattr("app.ext.unipile.get_settings", lambda: configured)
    provider = UnipileProvider("key")  # configured deployment, but no seat behind it

    with pytest.raises(ProviderError) as caught:
        await provider.search(Targeting(titles=["engineer"]))
    assert "no LinkedIn account connected" in str(caught.value)
