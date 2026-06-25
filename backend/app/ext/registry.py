"""Provider catalog + builders for the enabled set on a request.

Resolution order per provider: a BYO org credential (ProviderCredential), else a platform key from
settings, else nothing. The synthetic demo provider is appended as a fallback so discovery works
before any real key is configured. Only "live" providers (those with an adapter factory) are built;
keys for not-yet-live providers can still be stored, ready for when their adapter ships.
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.crypto import unseal
from app.core.types import JsonObject
from app.ext.apollo import ApolloProvider
from app.ext.base import SourceProvider
from app.ext.demo import DemoProvider
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

# provider key -> adapter factory (only providers with an adapter are constructed)
_FACTORIES: dict[str, Callable[[str], SourceProvider]] = {
    "pdl": lambda key: PDLProvider(key),
    "apollo": lambda key: ApolloProvider(key),
    "hunter": lambda key: HunterProvider(key),
    "linkedin": lambda key: UnipileProvider(key),
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


def provider_selection(settings: JsonObject) -> list[str] | None:
    """The ordered provider-key allow-list from a workspace's settings, or None for 'use all'."""
    raw = settings.get("providers")
    if isinstance(raw, list):
        keys = [str(x) for x in raw if isinstance(x, str)]
        return keys or None
    return None


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
    return factory(api_key) if factory else None


def build_providers(settings: Settings | None = None) -> Sequence[SourceProvider]:
    """Platform-key + demo only (no org context)."""
    settings = settings or get_settings()
    platform = _platform_keys(settings)
    providers: list[SourceProvider] = [
        factory(platform[key]) for key, factory in _FACTORIES.items() if platform.get(key)
    ]
    if settings.people_providers_demo or not providers:
        providers.append(DemoProvider())
    return providers


async def build_providers_for_org(
    session: AsyncSession,
    organization_id: str,
    settings: Settings | None = None,
    *,
    selection: list[str] | None = None,
) -> Sequence[SourceProvider]:
    """BYO org credentials first, then platform keys, then the demo fallback.

    `selection` (an ordered list of provider keys from a workspace's settings) filters + orders the
    result; an empty / non-matching selection falls back to all.
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
            providers.append(factory(api_key))
    if settings.people_providers_demo or not providers:
        providers.append(DemoProvider())
    return _apply_selection(providers, selection)
