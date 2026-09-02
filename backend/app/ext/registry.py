"""Provider catalog + builders for the enabled set on a request.

Resolution order per provider: a BYO org credential (ProviderCredential), else a platform key from
settings, else nothing. With no key configured for any provider the built set is empty and people
search returns no results. Only "live" providers (those with an adapter factory) are built; keys
for not-yet-live providers can still be stored, ready for when their adapter ships.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import unseal
from app.ext.apollo import ApolloProvider
from app.ext.base import SourceProvider
from app.ext.hunter import HunterProvider
from app.ext.pdl import PDLProvider
from app.ext.unipile import UnipileProvider
from app.models import ProviderCredential


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    name: str
    live: bool  # has a working adapter today
    docs_url: str


PROVIDER_CATALOG: list[ProviderSpec] = [
    ProviderSpec("pdl", "People Data Labs", True, "https://docs.peopledatalabs.com"),
    ProviderSpec("apollo", "Apollo.io", True, "https://docs.apollo.io"),
    ProviderSpec("hunter", "Hunter", True, "https://hunter.io/api-documentation"),
    # Rail A: configured via platform Unipile env (api key + dsn + connected seat), not a BYO key.
    ProviderSpec("linkedin", "LinkedIn search (Unipile)", False, "https://www.unipile.com/"),
]

# provider key -> adapter factory, as `(api_key, account_id) -> provider`.
#
# The second argument is the *connected seat* to act as. Only LinkedIn has one: its search runs as
# a member, through somebody's actual account, so which account is not a detail — it decides whose
# network is searched and whose rate limits are spent. The licensed data providers (PDL, Apollo,
# Hunter) are keyed per organization and ignore it.
_FACTORIES: dict[str, Callable[[str, str | None], SourceProvider]] = {
    "pdl": lambda key, _account: PDLProvider(key),
    "apollo": lambda key, _account: ApolloProvider(key),
    "hunter": lambda key, _account: HunterProvider(key),
    "linkedin": lambda key, account: UnipileProvider(key, account),
}


def _platform_keys(settings: Settings) -> dict[str, str]:
    keys: dict[str, str] = {}
    if settings.pdl_api_key:
        keys["pdl"] = settings.pdl_api_key
    if settings.apollo_api_key:
        keys["apollo"] = settings.apollo_api_key
    if settings.hunter_api_key:
        keys["hunter"] = settings.hunter_api_key
    if settings.unipile_api_key:
        keys["linkedin"] = settings.unipile_api_key
    return keys


def _apply_selection(
    providers: Sequence[SourceProvider], selection: list[str] | None
) -> Sequence[SourceProvider]:
    """Filter + order providers by a selection of keys. Falls back to all when the selection is
    empty or matches nothing, so a misconfigured workspace never sources with zero providers.
    """
    if not selection:
        return providers
    by_key = {p.key: p for p in providers}
    chosen = [by_key[k] for k in selection if k in by_key]
    return chosen or providers


def build_one(provider_key: str, api_key: str) -> SourceProvider | None:
    """Construct a single provider from a key (for credential verification)."""
    factory = _FACTORIES.get(provider_key)
    return factory(api_key, None) if factory else None


def build_providers(settings: Settings | None = None) -> Sequence[SourceProvider]:
    """Platform-key only (no org context, and so no seat — LinkedIn falls back to env)."""
    settings = settings or get_settings()
    platform = _platform_keys(settings)
    return [
        factory(platform[key], None) for key, factory in _FACTORIES.items() if platform.get(key)
    ]


async def build_providers_for_org(
    session: AsyncSession,
    organization_id: str,
    settings: Settings | None = None,
    *,
    selection: list[str] | None = None,
    linkedin_account_id: str | None = None,
) -> Sequence[SourceProvider]:
    """BYO org credentials first, then platform keys.

    `selection` (an ordered list of provider keys from a workspace's settings) filters + orders the
    result; an empty / non-matching selection falls back to all.

    `linkedin_account_id` is the connected seat LinkedIn search should act as — the caller's own
    seat for an interactive search, the campaign's for a sourcing pass. The caller resolves it,
    because who the right seat is depends on the context and the rules live in the service layer.
    Passing None leaves `UNIPILE_ACCOUNT_ID` as the fallback, which is a single-tenant convenience:
    it means every organization searches through whichever account the deployment configured, so a
    real multi-tenant deployment should leave that env var unset and rely on seats.
    """
    settings = settings or get_settings()
    rows = (
        (
            await session.execute(
                select(ProviderCredential).where(
                    ProviderCredential.organization_id == organization_id,
                    ProviderCredential.enabled.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    byo = {r.provider: r.secret for r in rows}
    platform = _platform_keys(settings)
    providers: list[SourceProvider] = []
    for key, factory in _FACTORIES.items():
        sealed = byo.get(key)
        api_key = unseal(sealed) if sealed else platform.get(key)
        if api_key:
            providers.append(factory(api_key, linkedin_account_id))
    return _apply_selection(providers, selection)
