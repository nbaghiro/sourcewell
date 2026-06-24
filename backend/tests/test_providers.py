"""Unit tests for the external people-data provider adapters (`app/ext/*`).

Every provider makes `httpx` calls internally and normalizes the provider's JSON into our
`PersonHit` / `EmailVerdict`. These tests mock the HTTP boundary with `respx` so nothing hits a
live API: each test stubs a realistic provider payload, calls the adapter method, and asserts the
*normalized* result (and, where useful, that the outgoing request reflects the `Targeting`).

Unipile is key-gated on `unipile_dsn` + `unipile_account_id` (a `_ready()` gate that reads
`app.core.config.get_settings`); the `unipile_settings` fixture monkeypatches that lookup so the
adapter is "configured" against a known mockable DSN.
"""

import json

import pytest
import respx
from httpx import Request, Response

from app.core.config import Settings
from app.core.types import JsonObject
from app.ext.apollo import ApolloProvider
from app.ext.base import json_object
from app.ext.hunter import HunterProvider
from app.ext.pdl import PDLProvider
from app.ext.unipile import UnipileProvider
from app.targeting import Targeting

_UNIPILE_DSN = "https://api1.unipile.com:1234"
_UNIPILE_ACCOUNT = "acct-123"


def _request_json(request: Request) -> JsonObject:
    """Decode a `respx`-recorded request body to a `JsonObject` (no `Any`)."""
    return json_object(json.loads(request.content.decode()))


# --------------------------------------------------------------------------- PDL


@respx.mock
async def test_pdl_search_normalizes_and_reflects_targeting() -> None:
    route = respx.post("https://api.peopledatalabs.com/v5/person/search").mock(
        return_value=Response(
            200,
            json={
                "total": 2,
                "data": [
                    {
                        "id": "pdl-1",
                        "full_name": "Jane Doe",
                        "job_title": "VP Sales",
                        "job_company_name": "Globex",
                        "location_name": "Berlin, DE",
                        "work_email": "jane@globex.com",
                        "skills": ["sales", "saas", "negotiation"],
                        "linkedin_url": "https://linkedin.com/in/janedoe",
                        "likelihood": 9,
                    },
                    {
                        "id": "pdl-2",
                        "full_name": "John Roe",
                        "job_title": "Sales Director",
                        "emails": [{"address": "john@initech.com"}],
                    },
                ],
            },
        )
    )

    page = await PDLProvider("test-key").search(Targeting(titles=["VP Sales"]), limit=10)

    assert page.total == 2
    assert len(page.hits) == 2
    first = page.hits[0]
    assert first.provider == "pdl"
    assert first.external_id == "pdl-1"
    assert first.full_name == "Jane Doe"
    assert first.title == "VP Sales"
    assert first.company == "Globex"
    assert first.location == "Berlin, DE"
    assert first.email == "jane@globex.com"
    assert first.email_status == "unverified"
    assert first.skills == ["sales", "saas", "negotiation"]
    assert first.confidence == 90  # likelihood 9 -> *10
    # falls back to emails[0].address when work_email is absent
    assert page.hits[1].email == "john@initech.com"

    # the outgoing ES query reflects the requested title
    body = _request_json(route.calls.last.request)
    assert "VP Sales" in json.dumps(body)
    assert body["size"] == 10


@respx.mock
async def test_pdl_enrich_maps_record() -> None:
    respx.get("https://api.peopledatalabs.com/v5/person/enrich").mock(
        return_value=Response(
            200,
            json={
                "data": {
                    "id": "pdl-9",
                    "full_name": "Mara Lin",
                    "job_title": "Head of Growth",
                    "job_company_name": "Lumen",
                    "work_email": "mara@lumen.io",
                    "likelihood": 7,
                }
            },
        )
    )

    hit = await PDLProvider("test-key").enrich(email="mara@lumen.io")
    assert hit is not None
    assert hit.full_name == "Mara Lin"
    assert hit.title == "Head of Growth"
    assert hit.company == "Lumen"
    assert hit.email == "mara@lumen.io"
    assert hit.confidence == 70


@respx.mock
async def test_pdl_enrich_returns_none_on_4xx() -> None:
    respx.get("https://api.peopledatalabs.com/v5/person/enrich").mock(
        return_value=Response(404, json={"error": "not found"})
    )
    assert await PDLProvider("test-key").enrich(email="nobody@nowhere.com") is None


@respx.mock
async def test_pdl_search_empty_on_500() -> None:
    respx.post("https://api.peopledatalabs.com/v5/person/search").mock(
        return_value=Response(500, text="boom")
    )
    page = await PDLProvider("test-key").search(Targeting(titles=["VP Sales"]))
    assert page.hits == []
    assert page.total == 0


# --------------------------------------------------------------------------- Apollo


@respx.mock
async def test_apollo_search_normalizes_and_reflects_targeting() -> None:
    route = respx.post("https://api.apollo.io/v1/mixed_people/search").mock(
        return_value=Response(
            200,
            json={
                "pagination": {"total_entries": 1},
                "people": [
                    {
                        "id": "apl-1",
                        "first_name": "Ada",
                        "last_name": "Byron",
                        "title": "VP Sales",
                        "email": "ada@globex.com",
                        "email_status": "verified",
                        "city": "London",
                        "country": "UK",
                        "linkedin_url": "https://linkedin.com/in/ada",
                        "organization": {
                            "name": "Globex",
                            "estimated_num_employees": 800,
                            "industry": "Fintech",
                        },
                    }
                ],
            },
        )
    )

    page = await ApolloProvider("test-key").search(Targeting(titles=["VP Sales"]), limit=25)

    assert page.total == 1
    hit = page.hits[0]
    assert hit.provider == "apollo"
    assert hit.full_name == "Ada Byron"  # first_name + last_name join
    assert hit.title == "VP Sales"
    assert hit.company == "Globex"
    assert hit.location == "London, UK"
    assert hit.email == "ada@globex.com"
    assert hit.email_status == "valid"  # "verified" -> valid
    assert hit.company_size == "800"
    assert hit.industry == "Fintech"

    body = _request_json(route.calls.last.request)
    assert body["person_titles"] == ["VP Sales"]
    assert body["api_key"] == "test-key"


@respx.mock
async def test_apollo_enrich_maps_person() -> None:
    respx.post("https://api.apollo.io/v1/people/match").mock(
        return_value=Response(
            200,
            json={
                "person": {
                    "id": "apl-2",
                    "name": "Grace Hopper",
                    "title": "Engineering Lead",
                    "email": "grace@navy.mil",
                    "email_status": "guessed",
                    "organization": {"name": "Navy"},
                }
            },
        )
    )

    hit = await ApolloProvider("test-key").enrich(email="grace@navy.mil")
    assert hit is not None
    assert hit.full_name == "Grace Hopper"
    assert hit.company == "Navy"
    assert hit.email_status == "risky"  # "guessed" -> risky


@respx.mock
async def test_apollo_verify_email_maps_status() -> None:
    respx.post("https://api.apollo.io/v1/people/match").mock(
        return_value=Response(
            200,
            json={"person": {"name": "X", "email": "x@y.com", "email_status": "verified"}},
        )
    )
    verdict = await ApolloProvider("test-key").verify_email("x@y.com")
    assert verdict.email == "x@y.com"
    assert verdict.status == "valid"


@respx.mock
async def test_apollo_enrich_returns_none_on_4xx() -> None:
    respx.post("https://api.apollo.io/v1/people/match").mock(
        return_value=Response(422, json={"error": "bad"})
    )
    assert await ApolloProvider("test-key").enrich(email="x@y.com") is None


@respx.mock
async def test_apollo_search_empty_on_error() -> None:
    respx.post("https://api.apollo.io/v1/mixed_people/search").mock(
        return_value=Response(401, json={"error": "unauthorized"})
    )
    page = await ApolloProvider("test-key").search(Targeting(titles=["VP Sales"]))
    assert page.hits == []
    assert page.total == 0


# --------------------------------------------------------------------------- Hunter


@respx.mock
async def test_hunter_enrich_finds_email() -> None:
    route = respx.get("https://api.hunter.io/v2/email-finder").mock(
        return_value=Response(
            200,
            json={"data": {"email": "lisa.su@amd.com", "score": 95}},
        )
    )

    hit = await HunterProvider("test-key").enrich(name="Lisa Su", company="AMD")
    assert hit is not None
    assert hit.provider == "hunter"
    assert hit.full_name == "Lisa Su"
    assert hit.company == "AMD"
    assert hit.email == "lisa.su@amd.com"
    assert hit.email_status == "valid"  # score >= 80

    params = dict(route.calls.last.request.url.params)
    assert params["first_name"] == "Lisa"
    assert params["last_name"] == "Su"
    assert params["company"] == "AMD"


async def test_hunter_enrich_needs_name_and_company() -> None:
    # No HTTP call happens when name/company are missing — guard before mocking.
    assert await HunterProvider("test-key").enrich(email="x@y.com") is None


@respx.mock
async def test_hunter_verify_email_deliverable() -> None:
    respx.get("https://api.hunter.io/v2/email-verifier").mock(
        return_value=Response(
            200,
            json={"data": {"result": "deliverable", "score": 88}},
        )
    )
    verdict = await HunterProvider("test-key").verify_email("ceo@acme.com")
    assert verdict.status == "valid"  # "deliverable" -> valid
    assert verdict.score == 88


@respx.mock
async def test_hunter_verify_email_undeliverable() -> None:
    respx.get("https://api.hunter.io/v2/email-verifier").mock(
        return_value=Response(
            200,
            json={"data": {"result": "undeliverable", "score": 12}},
        )
    )
    verdict = await HunterProvider("test-key").verify_email("ghost@acme.com")
    assert verdict.status == "invalid"
    assert verdict.score == 12


@respx.mock
async def test_hunter_verify_email_unknown_on_5xx() -> None:
    respx.get("https://api.hunter.io/v2/email-verifier").mock(
        return_value=Response(503, text="unavailable")
    )
    verdict = await HunterProvider("test-key").verify_email("who@acme.com")
    assert verdict.status == "unknown"


async def test_hunter_has_no_people_search() -> None:
    # search is a hard no-op (Hunter has no people-search API) — no HTTP, returns empty.
    page = await HunterProvider("test-key").search(Targeting(titles=["VP Sales"]))
    assert page.hits == []
    assert page.total == 0


# --------------------------------------------------------------------------- Unipile


@pytest.fixture
def unipile_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make `UnipileProvider` "configured": a known DSN + account so `_ready()` passes and the
    DSN is a stable URL we can mock. The adapter reads these via `get_settings()` when built."""
    configured = Settings(unipile_dsn=_UNIPILE_DSN, unipile_account_id=_UNIPILE_ACCOUNT)
    monkeypatch.setattr("app.ext.unipile.get_settings", lambda: configured)


@respx.mock
async def test_unipile_search_normalizes_and_reflects_targeting(unipile_settings: None) -> None:
    route = respx.post(f"{_UNIPILE_DSN}/api/v1/linkedin/search").mock(
        return_value=Response(
            200,
            json={
                "total": 1,
                "items": [
                    {
                        "id": "li-1",
                        "name": "Satya N",
                        "headline": "Chief Executive Officer",
                        "company": "Northwind",
                        "location": "Seattle, US",
                        "profile_url": "https://linkedin.com/in/satya",
                        "skills": ["leadership", "cloud"],
                    }
                ],
            },
        )
    )

    page = await UnipileProvider("test-key").search(Targeting(titles=["VP Sales"]), limit=20)

    assert page.total == 1
    hit = page.hits[0]
    assert hit.provider == "linkedin"
    assert hit.external_id == "li-1"
    assert hit.full_name == "Satya N"
    assert hit.title == "Chief Executive Officer"
    assert hit.company == "Northwind"
    assert hit.location == "Seattle, US"
    assert hit.linkedin_url == "https://linkedin.com/in/satya"
    assert hit.skills == ["leadership", "cloud"]

    body = _request_json(route.calls.last.request)
    assert body["account_id"] == _UNIPILE_ACCOUNT
    assert "VP Sales" in str(body["keywords"])  # title folded into keyword string
    assert body["limit"] == 20


@respx.mock
async def test_unipile_search_reads_results_key_and_current_company(
    unipile_settings: None,
) -> None:
    # The adapter accepts `results` as an alias for `items`, and `current_company.name` for company.
    respx.post(f"{_UNIPILE_DSN}/api/v1/linkedin/search").mock(
        return_value=Response(
            200,
            json={
                "results": [
                    {
                        "first_name": "Ada",
                        "last_name": "Byron",
                        "title": "Engineer",
                        "current_company": {"name": "Analytical Engines"},
                    }
                ]
            },
        )
    )
    page = await UnipileProvider("test-key").search(Targeting())
    hit = page.hits[0]
    assert hit.full_name == "Ada Byron"  # first + last fallback
    assert hit.company == "Analytical Engines"  # current_company.name fallback


@respx.mock
async def test_unipile_enrich_maps_user(unipile_settings: None) -> None:
    respx.get(f"{_UNIPILE_DSN}/api/v1/users/satya").mock(
        return_value=Response(
            200,
            json={
                "id": "li-1",
                "name": "Satya N",
                "headline": "CEO",
                "company": "Northwind",
                "public_profile_url": "https://linkedin.com/in/satya",
            },
        )
    )
    hit = await UnipileProvider("test-key").enrich(linkedin_url="satya")
    assert hit is not None
    assert hit.full_name == "Satya N"
    assert hit.title == "CEO"
    assert hit.company == "Northwind"
    assert hit.linkedin_url == "https://linkedin.com/in/satya"


@respx.mock
async def test_unipile_enrich_returns_none_on_4xx(unipile_settings: None) -> None:
    respx.get(f"{_UNIPILE_DSN}/api/v1/users/ghost").mock(
        return_value=Response(404, json={"error": "not found"})
    )
    assert await UnipileProvider("test-key").enrich(linkedin_url="ghost") is None


@respx.mock
async def test_unipile_search_empty_on_error(unipile_settings: None) -> None:
    respx.post(f"{_UNIPILE_DSN}/api/v1/linkedin/search").mock(
        return_value=Response(500, text="boom")
    )
    page = await UnipileProvider("test-key").search(Targeting(titles=["VP Sales"]))
    assert page.hits == []
    assert page.total == 0


async def test_unipile_search_unconfigured_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    # Without a DSN/account the `_ready()` gate short-circuits — no HTTP call, empty page.
    monkeypatch.setattr("app.ext.unipile.get_settings", lambda: Settings(unipile_dsn=""))
    page = await UnipileProvider("test-key").search(Targeting(titles=["VP Sales"]))
    assert page.hits == []
    assert page.total == 0


async def test_unipile_enrich_unconfigured_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.ext.unipile.get_settings", lambda: Settings(unipile_dsn=""))
    assert await UnipileProvider("test-key").enrich(linkedin_url="x") is None
