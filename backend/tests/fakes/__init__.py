"""Deterministic test fakes for IO-free tests (clock, people-data source; LLM lives in fake_llm)."""

from datetime import UTC, datetime, timedelta

from app.ext.base import EmailVerdict, PersonHit, ProviderCapabilities, SearchPage
from app.targeting import Targeting


class FakeClock:
    """An injectable clock for time-dependent logic (e.g. the runtime scheduler)."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime(2026, 1, 1, tzinfo=UTC)

    def now(self) -> datetime:
        return self._now

    def advance(self, **kwargs: float) -> None:
        self._now += timedelta(**kwargs)


def make_hit(
    i: int,
    full_name: str,
    title: str,
    company: str,
    location: str,
    skills: list[str],
    *,
    provider: str = "fake",
    email: str | None = None,
) -> PersonHit:
    first = full_name.split()[0].lower()
    return PersonHit(
        provider=provider,
        external_id=f"{provider}-{i}",
        full_name=full_name,
        title=title,
        company=company,
        location=location,
        email=email if email is not None else f"{first}@{company.lower().replace(' ', '')}.com",
        email_status="unverified",
        linkedin_url=f"https://linkedin.com/in/{full_name.lower().replace(' ', '')}",
        skills=skills,
        company_size="51-200",
        industry="B2B SaaS",
        confidence=80,
    )


def fake_roster() -> list[PersonHit]:
    return [
        make_hit(0, "Aisha Berg", "VP of Sales", "Northwind", "EU", ["Salesforce", "Enterprise"]),
        make_hit(1, "Marcus Lee", "Head of Sales", "Globex", "EU", ["Salesforce"]),
        make_hit(2, "Sofia Wong", "Account Executive", "Initech", "EU", ["Outbound"]),
        make_hit(3, "Diego Santos", "Senior Backend Engineer", "Acme Cloud", "EU", ["Python"]),
        make_hit(4, "Lena Park", "Data Analyst", "Lumen", "EU", ["SQL"]),
        make_hit(5, "Raj Kumar", "VP of Sales", "Meridian", "EU", ["Enterprise"]),
        make_hit(6, "Theo Holt", "Sales Director", "Vertex", "EU", ["Salesforce", "Outbound"]),
        make_hit(7, "Priya Raman", "Staff Engineer", "Lattice", "EU", ["Go", "Postgres"]),
    ]


class FakeSourceProvider:
    """A deterministic in-memory SourceProvider standing in for a real search API."""

    name = "Fake data"
    capabilities = ProviderCapabilities(search=True, enrich=True, verify_email=True)

    def __init__(self, hits: list[PersonHit] | None = None, key: str = "fake") -> None:
        self.key = key
        self._hits = hits if hits is not None else fake_roster()

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage:
        hits = [h.model_copy(deep=True) for h in self._hits[:limit]]
        return SearchPage(hits=hits, total=len(hits))

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
        ok = "@" in email and "." in email.split("@")[-1]
        return EmailVerdict(email=email, status="valid" if ok else "invalid", score=90 if ok else 0)

    async def verify_credentials(self) -> bool:
        return True


__all__ = ["FakeClock", "FakeSourceProvider", "fake_roster", "make_hit"]
