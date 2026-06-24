"""Apollo.io adapter — people search + enrich + email status. Key-gated; graceful on error."""

import httpx

from app.core.types import JsonObject
from app.services.sourcing.adapters.base import (
    EmailVerdict,
    PersonHit,
    ProviderCapabilities,
    SearchPage,
    json_body,
    json_list,
    json_object,
    opt_str,
)
from app.targeting import Targeting

_BASE = "https://api.apollo.io/v1"
_TIMEOUT = 25.0
_STATUS = {
    "verified": "valid",
    "likely to engage": "valid",
    "extrapolated": "risky",
    "guessed": "risky",
    "unavailable": "unknown",
    "unverified": "unverified",
}


class ApolloProvider:
    key = "apollo"
    name = "Apollo.io"
    capabilities = ProviderCapabilities(search=True, enrich=True, verify_email=True)

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def _normalize(self, p: JsonObject) -> PersonHit:
        org = json_object(p.get("organization"))
        size = org.get("estimated_num_employees")
        name = (
            opt_str(p.get("name"))
            or " ".join(filter(None, [opt_str(p.get("first_name")), opt_str(p.get("last_name"))]))
            or ""
        )
        location = ", ".join(filter(None, [opt_str(p.get("city")), opt_str(p.get("country"))]))
        return PersonHit(
            provider=self.key,
            external_id=opt_str(p.get("id")),
            full_name=name,
            title=opt_str(p.get("title")),
            company=opt_str(org.get("name")) or opt_str(p.get("organization_name")),
            location=location or None,
            email=opt_str(p.get("email")),
            email_status=_STATUS.get(str(p.get("email_status") or ""), "unverified"),
            linkedin_url=opt_str(p.get("linkedin_url")),
            company_size=str(size) if size else None,
            industry=opt_str(org.get("industry")),
        )

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage:
        payload: JsonObject = {
            "api_key": self._key,
            "page": 1,
            "per_page": min(limit, 100),
        }
        if targeting.titles:
            payload["person_titles"] = targeting.titles
        if targeting.locations:
            payload["person_locations"] = targeting.locations
        if targeting.seniorities:
            payload["person_seniorities"] = targeting.seniorities
        if targeting.functions:
            payload["person_departments"] = targeting.functions
        if targeting.companies:
            payload["organization_names"] = targeting.companies
        if targeting.industries:
            payload["organization_industry_tag_ids"] = targeting.industries
        if targeting.company_sizes:
            payload["organization_num_employees_ranges"] = targeting.company_sizes
        if targeting.technologies:
            payload["currently_using_any_of_technology_uids"] = targeting.technologies
        if targeting.exclude_titles:
            payload["person_not_titles"] = targeting.exclude_titles
        if targeting.exclude_companies:
            payload["organization_not_names"] = targeting.exclude_companies
        # Fold free-text keywords + skills (Apollo has no first-class skills facet) into q_keywords.
        keyword_text = " ".join(filter(None, [targeting.keywords, *targeting.skills])).strip()
        if keyword_text:
            payload["q_keywords"] = keyword_text
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(f"{_BASE}/mixed_people/search", json=payload)
        except Exception:
            return SearchPage(hits=[], total=0)
        if resp.status_code >= 400:
            return SearchPage(hits=[], total=0)
        data = json_body(resp)
        hits = [self._normalize(p) for p in json_list(data.get("people"))]
        total = json_object(data.get("pagination")).get("total_entries")
        return SearchPage(hits=hits, total=total if isinstance(total, int) else None)

    async def enrich(
        self,
        *,
        email: str | None = None,
        linkedin_url: str | None = None,
        name: str | None = None,
        company: str | None = None,
    ) -> PersonHit | None:
        payload: JsonObject = {"api_key": self._key}
        if email:
            payload["email"] = email
        if linkedin_url:
            payload["linkedin_url"] = linkedin_url
        if name:
            payload["name"] = name
        if company:
            payload["organization_name"] = company
        if len(payload) == 1:
            return None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(f"{_BASE}/people/match", json=payload)
        except Exception:
            return None
        if resp.status_code >= 400:
            return None
        person = json_object(json_body(resp).get("person"))
        return self._normalize(person) if person else None

    async def verify_email(self, email: str) -> EmailVerdict:
        hit = await self.enrich(email=email)
        status = hit.email_status if hit else "unknown"
        return EmailVerdict(email=email, status=status or "unknown")

    async def verify_credentials(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_BASE}/mixed_people/search", json={"api_key": self._key, "per_page": 1}
                )
            return resp.status_code < 400
        except Exception:
            return False
