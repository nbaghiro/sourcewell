"""Adapter contract for Rail B people-data providers (PDL, Apollo, Cognism, ...).

Discovery is *live pass-through*: a provider is queried per request and its payload is NORMALIZED
into `PersonHit` (the shape of our `Contact` table). Nothing here persists — only an explicit
import writes Contacts (see `people.py`). We never mirror a provider's corpus into our own index;
their terms forbid it and it would be stale and costly. Caching, if added, is transient and keyed
by query for cost/perf only.
"""

from typing import Protocol, cast

import httpx
from pydantic import BaseModel

from app.core.types import JsonList, JsonObject
from app.services.sourcing.targeting import Targeting


def opt_str(v: object) -> str | None:
    """Narrow a decoded-JSON value to an optional string (drops empty/non-string values)."""
    return v if isinstance(v, str) and v else None


def opt_int(v: object, default: int = 0) -> int:
    """Narrow a decoded-JSON value to an int (truncating floats; `default` if non-numeric)."""
    return int(v) if isinstance(v, int | float) else default


def str_list(v: object, limit: int | None = None) -> list[str]:
    """Narrow a decoded-JSON value to a list of strings (optionally truncated)."""
    if not isinstance(v, list):
        return []
    out = [s for s in v if isinstance(s, str)]
    return out[:limit] if limit is not None else out


def json_object(raw: object) -> JsonObject:
    """Narrow an untyped JSON payload (e.g. httpx's `Any` `.json()`) to a `JsonObject`.

    Returns `{}` for non-objects, so call sites chain `.get(...)` (-> `object`) and the
    `opt_str`/`opt_int`/`str_list` narrowers without ever touching `Any`.
    """
    return cast(JsonObject, raw) if isinstance(raw, dict) else {}


def json_list(raw: object) -> JsonList:
    """Narrow an untyped JSON value to a list of `JsonObject` (non-dict items dropped)."""
    if not isinstance(raw, list):
        return []
    return [cast(JsonObject, x) for x in raw if isinstance(x, dict)]


def json_body(resp: httpx.Response) -> JsonObject:
    """The single httpx→JSON boundary: a response body decoded to a `JsonObject` ({} if not one).

    `httpx.Response.json()` is typed `Any`; isolating it here means call sites never touch `Any`.
    """
    return json_object(resp.json())


class PersonHit(BaseModel):
    """A normalized search/enrich result — same fields as `Contact`, plus provenance + scoring.

    Satisfies the evaluator's `Candidate` protocol (skills/title/location/email), so a hit can be
    fit-scored with the exact same `evaluate()` the ranking pipeline uses.
    """

    provider: str
    external_id: str | None = None
    full_name: str
    title: str | None = None
    company: str | None = None
    location: str | None = None
    email: str | None = None
    email_status: str | None = None  # valid | risky | invalid | unverified
    linkedin_url: str | None = None
    avatar_url: str | None = None
    skills: list[str] = []
    company_size: str | None = None
    industry: str | None = None
    phone: str | None = None
    confidence: int = 0  # provider-reported match confidence, 0..100
    score: int = 0  # our evaluate() fit score, 0..100
    rationale: str | None = None


class EmailVerdict(BaseModel):
    email: str
    status: str = "unknown"  # valid | risky | invalid | unknown
    score: int = 0


class SearchPage(BaseModel):
    hits: list[PersonHit] = []
    total: int | None = None
    cursor: str | None = None


class ProviderCapabilities(BaseModel):
    search: bool = False
    enrich: bool = False
    verify_email: bool = False


class SourceProvider(Protocol):
    """What every provider adapter implements. Concrete adapters are plain classes (duck-typed)."""

    key: str
    name: str
    capabilities: ProviderCapabilities

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage: ...

    async def enrich(
        self,
        *,
        email: str | None = None,
        linkedin_url: str | None = None,
        name: str | None = None,
        company: str | None = None,
    ) -> PersonHit | None: ...

    async def verify_email(self, email: str) -> EmailVerdict: ...

    async def verify_credentials(self) -> bool: ...
