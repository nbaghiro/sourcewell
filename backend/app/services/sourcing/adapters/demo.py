"""Synthetic provider — works with no API key (and powers the search UI in demo mode).

Deterministic: the same query yields the same people, shaped to reflect the criteria so results
score sensibly through `evaluate()`. This is the fallback the registry uses when no real provider
key is configured, so the discovery flow is fully exercisable end-to-end without credentials.
"""

from app.services.sourcing.adapters.base import (
    EmailVerdict,
    PersonHit,
    ProviderCapabilities,
    SearchPage,
)
from app.services.sourcing.targeting import Targeting

_FIRST = [
    "Aisha",
    "Marcus",
    "Sofia",
    "Diego",
    "Lena",
    "Raj",
    "Mia",
    "Theo",
    "Priya",
    "Noah",
    "Elena",
    "Kofi",
    "Anika",
    "Sven",
    "Rosa",
    "Idris",
]
_LAST = [
    "Berg",
    "Lee",
    "Wong",
    "Santos",
    "Park",
    "Kumar",
    "Becker",
    "Ruiz",
    "Raman",
    "Foster",
    "Holt",
    "Adeyemi",
    "Voss",
    "Mwangi",
    "Chen",
    "Hassan",
]
_COMPANIES = [
    ("Northwind", "501-1,000", "B2B SaaS"),
    ("Globex", "501-1,000", "Fintech"),
    ("Initech", "201-500", "Enterprise Software"),
    ("Acme Cloud", "5,000+", "Cloud Infrastructure"),
    ("Lumen", "51-200", "DevTools"),
    ("Meridian", "51-200", "Systems Integrator"),
]
_LOCATIONS = [
    "Berlin, DE",
    "London, UK",
    "Amsterdam, NL",
    "Lisbon, PT",
    "Dublin, IE",
    "Remote · EU",
]
_DEFAULT_TITLES = ["Senior Backend Engineer", "VP of Sales", "Head of Partnerships"]
_DEFAULT_SKILLS = ["Python", "Go", "Postgres"]


def _hash(s: str) -> int:
    h = 7
    for c in s:
        h = (h * 31 + ord(c)) & 0xFFFFFFFF
    return h


def _slug(s: str) -> str:
    return "".join(c for c in s.lower() if c.isalnum())


class DemoProvider:
    key = "demo"
    name = "Demo data"
    capabilities = ProviderCapabilities(search=True, enrich=True, verify_email=True)

    def _person(self, seed: int, *, title: str, skills: list[str], location: str) -> PersonHit:
        first = _FIRST[seed % len(_FIRST)]
        last = _LAST[(seed // 7) % len(_LAST)]
        company, size, industry = _COMPANIES[seed % len(_COMPANIES)]
        email = f"{first.lower()}.{last.lower()}@{_slug(company)}.com"
        return PersonHit(
            provider=self.key,
            external_id=f"demo-{seed}",
            full_name=f"{first} {last}",
            title=title,
            company=company,
            location=location,
            email=email,
            email_status="unverified",
            linkedin_url=f"https://linkedin.com/in/{first.lower()}{last.lower()}",
            skills=skills[:3],
            company_size=size,
            industry=industry,
            confidence=70 + (seed % 30),
        )

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage:
        titles = targeting.titles or _DEFAULT_TITLES
        skills = targeting.skills or _DEFAULT_SKILLS
        locations = targeting.locations or _LOCATIONS
        n = min(max(limit, 1), 50)
        # Seed off EVERY targeting field (incl. free-text keywords + negative targeting) so any
        # change — adding, editing, or clearing a filter — reshuffles the result set, the way a
        # real provider's corpus would respond. Real adapters map these onto their own query DSL.
        signature = "|".join(
            [
                cursor or "",
                ",".join(targeting.titles),
                ",".join(targeting.skills),
                ",".join(targeting.locations),
                ",".join(targeting.companies),
                ",".join(targeting.industries),
                ",".join(targeting.company_sizes),
                ",".join(targeting.seniorities),
                ",".join(targeting.functions),
                ",".join(targeting.technologies),
                ",".join(targeting.exclude_companies),
                ",".join(targeting.exclude_titles),
                (targeting.keywords or "").strip().lower(),
            ]
        )
        base = _hash(signature)
        hits = [
            self._person(
                base + i * 101,
                title=titles[i % len(titles)],
                skills=skills,
                location=locations[i % len(locations)],
            )
            for i in range(n)
        ]
        return SearchPage(hits=hits, total=n, cursor=None)

    async def enrich(
        self,
        *,
        email: str | None = None,
        linkedin_url: str | None = None,
        name: str | None = None,
        company: str | None = None,
    ) -> PersonHit | None:
        seed = _hash(email or linkedin_url or f"{name}|{company}" or "x")
        hit = self._person(
            seed, title=_DEFAULT_TITLES[0], skills=_DEFAULT_SKILLS, location=_LOCATIONS[0]
        )
        if email:
            hit.email = email
            hit.email_status = "valid"
        if name:
            hit.full_name = name
        if company:
            hit.company = company
        return hit

    async def verify_email(self, email: str) -> EmailVerdict:
        ok = "@" in email and "." in email.split("@")[-1]
        return EmailVerdict(email=email, status="valid" if ok else "invalid", score=90 if ok else 0)

    async def verify_credentials(self) -> bool:
        return True
