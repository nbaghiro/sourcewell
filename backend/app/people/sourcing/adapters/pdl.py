"""People Data Labs adapter — live pass-through search + person enrich.

https://docs.peopledatalabs.com — Person Search (POST /v5/person/search) and Person Enrichment
(GET /v5/person/enrich). We do NOT store PDL data beyond the records a user explicitly imports;
search responses are returned to the request and discarded. No avatar is mapped on purpose
(profile photos are not ours to redistribute).
"""

import httpx

from app.core.types import JsonObject
from app.people.sourcing.adapters.base import (
    EmailVerdict,
    PersonHit,
    ProviderCapabilities,
    SearchPage,
    json_body,
    json_list,
    json_object,
    opt_str,
    str_list,
)
from app.targeting import Targeting

_BASE = "https://api.peopledatalabs.com/v5"
_TIMEOUT = 20.0


class PDLProvider:
    key = "pdl"
    name = "People Data Labs"
    capabilities = ProviderCapabilities(search=True, enrich=True, verify_email=False)

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def _es_query(self, t: Targeting) -> JsonObject:
        """Map normalized targeting onto a PDL Elasticsearch bool query."""
        must: list[JsonObject] = []
        if t.titles:
            must.append(
                {
                    "bool": {
                        "should": [{"match": {"job_title": x}} for x in t.titles],
                        "minimum_should_match": 1,
                    }
                }
            )
        if t.skills:
            must.append({"terms": {"skills": [s.lower() for s in t.skills]}})
        if t.locations:
            must.append(
                {
                    "bool": {
                        "should": [{"match": {"location_name": loc}} for loc in t.locations],
                        "minimum_should_match": 1,
                    }
                }
            )
        if t.industries:
            must.append({"terms": {"job_company_industry": [i.lower() for i in t.industries]}})
        if t.company_sizes:
            must.append({"terms": {"job_company_size": t.company_sizes}})
        if t.companies:
            must.append(
                {
                    "bool": {
                        "should": [{"match": {"job_company_name": c}} for c in t.companies],
                        "minimum_should_match": 1,
                    }
                }
            )
        if t.seniorities:
            must.append({"terms": {"job_title_levels": [s.lower() for s in t.seniorities]}})
        if t.functions:
            must.append({"terms": {"job_title_role": [f.lower() for f in t.functions]}})
        # TODO: PDL has no first-class "technologies" facet on Person Search; fold into keywords.
        keyword_text = " ".join(filter(None, [t.keywords, *t.technologies])).strip()
        if keyword_text:
            must.append({"query_string": {"query": keyword_text}})
        if not must:
            must.append({"exists": {"field": "work_email"}})
        must_not: list[JsonObject] = []
        if t.exclude_companies:
            must_not.append(
                {
                    "bool": {
                        "should": [{"match": {"job_company_name": c}} for c in t.exclude_companies],
                        "minimum_should_match": 1,
                    }
                }
            )
        if t.exclude_titles:
            must_not.append(
                {
                    "bool": {
                        "should": [{"match": {"job_title": x}} for x in t.exclude_titles],
                        "minimum_should_match": 1,
                    }
                }
            )
        bool_q: JsonObject = {"must": must}
        if must_not:
            bool_q["must_not"] = must_not
        return {"bool": bool_q}

    def _normalize(self, rec: JsonObject) -> PersonHit:
        email = opt_str(rec.get("work_email"))
        if not email:
            emails = rec.get("emails")
            if isinstance(emails, list) and emails and isinstance(emails[0], dict):
                email = opt_str(emails[0].get("address"))
        likelihood = rec.get("likelihood")
        confidence = round(float(likelihood) * 10) if isinstance(likelihood, int | float) else 0
        return PersonHit(
            provider=self.key,
            external_id=opt_str(rec.get("id")),
            full_name=opt_str(rec.get("full_name")) or "",
            title=opt_str(rec.get("job_title")),
            company=opt_str(rec.get("job_company_name")),
            location=opt_str(rec.get("location_name")),
            email=email,
            email_status="unverified" if email else None,
            linkedin_url=opt_str(rec.get("linkedin_url")),
            skills=str_list(rec.get("skills"), 12),
            company_size=opt_str(rec.get("job_company_size")),
            industry=opt_str(rec.get("job_company_industry")),
            phone=opt_str(rec.get("mobile_phone")),
            confidence=confidence,
        )

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage:
        body: JsonObject = {"query": self._es_query(targeting), "size": min(limit, 100)}
        if cursor:
            body["search_after"] = cursor
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE}/person/search", headers={"X-Api-Key": self._key}, json=body
            )
        if resp.status_code >= 400:
            return SearchPage(hits=[], total=0)
        data = json_body(resp)
        hits = [self._normalize(rec) for rec in json_list(data.get("data"))]
        total = data.get("total")
        return SearchPage(hits=hits, total=total if isinstance(total, int) else None)

    async def enrich(
        self,
        *,
        email: str | None = None,
        linkedin_url: str | None = None,
        name: str | None = None,
        company: str | None = None,
    ) -> PersonHit | None:
        params: dict[str, str] = {}
        if email:
            params["email"] = email
        if linkedin_url:
            params["profile"] = linkedin_url
        if name:
            params["name"] = name
        if company:
            params["company"] = company
        if not params:
            return None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{_BASE}/person/enrich", headers={"X-Api-Key": self._key}, params=params
            )
        if resp.status_code >= 400:
            return None
        rec = json_object(json_body(resp).get("data"))
        return self._normalize(rec) if rec else None

    async def verify_email(self, email: str) -> EmailVerdict:
        return EmailVerdict(email=email, status="unknown")  # not a PDL capability

    async def verify_credentials(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_BASE}/person/search",
                    headers={"X-Api-Key": self._key},
                    json={
                        "query": {"bool": {"must": [{"exists": {"field": "work_email"}}]}},
                        "size": 1,
                    },
                )
            return resp.status_code < 400
        except Exception:
            return False
