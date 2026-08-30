"""People discovery orchestration (Rail B).

- search: fan out to enabled providers, dedupe across them, fit-score each hit with the same
  `evaluate()` the ranking pipeline uses, return ranked. Live — nothing is persisted.
- enrich: waterfall providers until one returns a usable record (email/linkedin).
- import: persist only the selected hits as workspace Contacts (source = provider), deduped against
  what's already there. This is the ONLY step that writes to our DB.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import logger
from app.ext.base import PersonHit, ProviderError, SourceProvider
from app.models import Contact
from app.targeting import Targeting, evaluate

# Transient, process-local search cache (cost/perf only — NOT a corpus). Short TTL, bounded.
_CACHE: dict[str, tuple[float, list[PersonHit]]] = {}
_CACHE_TTL = 120.0
_CACHE_MAX = 256
_VALID_EMAIL_STATUS = {"valid", "risky", "invalid", "unverified", "unknown"}


class _Keyed(Protocol):
    @property
    def email(self) -> str | None: ...
    @property
    def linkedin_url(self) -> str | None: ...
    @property
    def full_name(self) -> str | None: ...
    @property
    def company(self) -> str | None: ...


def dedupe_key(obj: _Keyed) -> str:
    """Stable identity for a person: email, else LinkedIn URL, else name+company."""
    email = (obj.email or "").lower().strip()
    if email:
        return f"e:{email}"
    li = (obj.linkedin_url or "").lower().strip().rstrip("/")
    if li:
        return f"l:{li}"
    return f"n:{(obj.full_name or '').lower().strip()}|{(obj.company or '').lower().strip()}"


def _cache_key(providers: Sequence[SourceProvider], targeting: Targeting, limit: int) -> str:
    return f"{','.join(sorted(p.key for p in providers))}|{limit}|{targeting.model_dump_json()}"


@dataclass(frozen=True)
class ProviderFailure:
    """One provider that couldn't answer, and why — surfaced rather than counted as zero hits."""

    provider: str
    message: str


@dataclass(frozen=True)
class SearchOutcome:
    """What a fan-out actually produced: the ranked hits, plus whoever failed producing them.

    Both halves matter. An empty `hits` with a non-empty `failures` is a broken search; an empty
    one with no failures genuinely means nobody matched.
    """

    hits: list[PersonHit]
    failures: list[ProviderFailure]


async def search_people(
    providers: Sequence[SourceProvider],
    targeting: Targeting,
    *,
    limit: int = 25,
    use_cache: bool = True,
) -> SearchOutcome:
    """Live multi-provider search, deduped and fit-scored, ranked best-first (briefly cached).

    A provider that fails is recorded and skipped, never fatal: with several providers configured,
    one revoked key must not take the whole search down. Only clean runs are cached — caching a
    result that was short a provider would keep serving the gap for the TTL.
    """
    key = _cache_key(providers, targeting, limit)
    if use_cache and (entry := _CACHE.get(key)) and (time.monotonic() - entry[0]) < _CACHE_TTL:
        return SearchOutcome(hits=entry[1], failures=[])

    seen: set[str] = set()
    out: list[PersonHit] = []
    failures: list[ProviderFailure] = []
    for provider in providers:
        if not provider.capabilities.search:
            continue
        try:
            page = await provider.search(targeting, limit=limit)
        except ProviderError as exc:
            logger.warning("search: provider %s failed (%s)", exc.provider, exc)
            failures.append(ProviderFailure(provider=exc.provider, message=str(exc)))
            continue
        except Exception as exc:  # an adapter bug must not take the other providers down
            logger.exception("search: provider %s raised", provider.key)
            failures.append(ProviderFailure(provider=provider.key, message=str(exc) or "failed"))
            continue
        for hit in page.hits:
            dk = dedupe_key(hit)
            if dk in seen:
                continue
            seen.add(dk)
            hit.score, hit.rationale = evaluate(hit, targeting)
            out.append(hit)
    out.sort(key=lambda h: h.score, reverse=True)

    if use_cache and not failures:
        if len(_CACHE) >= _CACHE_MAX:
            _CACHE.pop(next(iter(_CACHE)))
        _CACHE[key] = (time.monotonic(), out)
    return SearchOutcome(hits=out, failures=failures)


async def verify_hits(
    providers: Sequence[SourceProvider], hits: list[PersonHit]
) -> list[PersonHit]:
    """Fill `email_status` for hits with an email via the first verify-capable provider."""
    verifier = next((p for p in providers if p.capabilities.verify_email), None)
    if verifier is None:
        return hits
    for hit in hits:
        if hit.email and (not hit.email_status or hit.email_status == "unverified"):
            verdict = await verifier.verify_email(hit.email)
            hit.email_status = verdict.status
    return hits


async def enrich_ref(
    providers: Sequence[SourceProvider],
    *,
    email: str | None = None,
    linkedin_url: str | None = None,
    name: str | None = None,
    company: str | None = None,
) -> PersonHit | None:
    """Waterfall: return the first provider record that resolves an email or LinkedIn URL."""
    for provider in providers:
        if not provider.capabilities.enrich:
            continue
        hit = await provider.enrich(
            email=email, linkedin_url=linkedin_url, name=name, company=company
        )
        if hit and (hit.email or hit.linkedin_url):
            return hit
    return None


async def import_hits(
    session: AsyncSession,
    *,
    workspace_id: str,
    hits: list[PersonHit],
) -> list[Contact]:
    """Persist selected hits as Contacts (source = provider), deduped against existing contacts."""
    existing = (
        (await session.execute(select(Contact).where(Contact.workspace_id == workspace_id)))
        .scalars()
        .all()
    )
    seen = {dedupe_key(c) for c in existing}
    created: list[Contact] = []
    for hit in hits:
        key = dedupe_key(hit)
        if key in seen:
            continue
        seen.add(key)
        status = hit.email_status if hit.email_status in _VALID_EMAIL_STATUS else "unverified"
        # Keep the sourced-but-not-yet-a-column signals (level / department / tech stack) so fit
        # scoring can use them later without another provider round-trip.
        attributes: dict[str, object] = {}
        if hit.seniority:
            attributes["seniority"] = hit.seniority
        if hit.function:
            attributes["function"] = hit.function
        if hit.technologies:
            attributes["technologies"] = list(hit.technologies)
        contact = Contact(
            workspace_id=workspace_id,
            full_name=hit.full_name,
            title=hit.title,
            company=hit.company,
            location=hit.location,
            email=hit.email,
            email_status=status,
            linkedin_url=hit.linkedin_url,
            avatar_url=hit.avatar_url,
            skills=list(hit.skills),
            source=hit.provider,
            company_size=hit.company_size,
            industry=hit.industry,
            attributes=attributes,
            tags=[],
            notes=None,
        )
        session.add(contact)
        created.append(contact)
    if created:
        await session.flush()
    return created
