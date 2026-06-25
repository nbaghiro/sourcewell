"""Cognism adapter — search + enrich, Bearer-auth, key-gated, graceful on error.

Cognism uses a Search → Redeem credit model: search returns previews for free, and revealing a
contact's email/phone consumes a credit (here, the `enrich` call is the reveal step). The exact
endpoint base + paths live behind Cognism's developer portal (gated + Entitlements-provisioned), so
they are best-effort and marked TODO; the adapter fails soft (empty / None) until confirmed against
a live account, so an unconfirmed path never crashes a search.
"""

import httpx

from app.core.types import JsonObject
from app.ext.base import (
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

# TODO: confirm base + paths against the Cognism developer portal (gated). Best-effort for now.
_BASE = "https://api.cognism.com/v1"
_TIMEOUT = 25.0


class CognismProvider:
    key = "cognism"
    name = "Cognism"
    capabilities = ProviderCapabilities(search=True, enrich=True, verify_email=False)

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "accept": "application/json",
            "Content-Type": "application/json",
        }

    def _normalize(self, rec: JsonObject) -> PersonHit:
        name = opt_str(rec.get("name")) or " ".join(
            filter(None, [opt_str(rec.get("firstName")), opt_str(rec.get("lastName"))])
        )
        company = json_object(rec.get("company"))
        return PersonHit(
            provider=self.key,
            external_id=opt_str(rec.get("id")) or opt_str(rec.get("contactId")),
            full_name=name or "",
            title=opt_str(rec.get("jobTitle")) or opt_str(rec.get("title")),
            company=opt_str(rec.get("companyName")) or opt_str(company.get("name")),
            location=opt_str(rec.get("location")),
            email=opt_str(rec.get("email")),
            linkedin_url=opt_str(rec.get("linkedinUrl")),
            skills=str_list(rec.get("skills"), 12),
        )

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage:
        keywords = " ".join(
            [
                *targeting.titles,
                *targeting.skills,
                *targeting.companies,
                *([targeting.keywords] if targeting.keywords else []),
            ]
        ).strip()
        payload: JsonObject = {"query": keywords, "limit": min(limit, 25)}
        if targeting.titles:
            payload["jobTitles"] = targeting.titles
        if targeting.locations:
            payload["locations"] = targeting.locations
        if cursor:
            payload["cursor"] = cursor
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(f"{_BASE}/search", headers=self._headers(), json=payload)
        except Exception:
            return SearchPage(hits=[], total=0)
        if resp.status_code >= 400:
            return SearchPage(hits=[], total=0)
        data = json_body(resp)
        items = json_list(data.get("results")) or json_list(data.get("contacts"))
        total = data.get("total")
        return SearchPage(
            hits=[self._normalize(r) for r in items],
            total=total if isinstance(total, int) else None,
            cursor=opt_str(data.get("cursor")),
        )

    async def enrich(
        self,
        *,
        email: str | None = None,
        linkedin_url: str | None = None,
        name: str | None = None,
        company: str | None = None,
    ) -> PersonHit | None:
        payload: JsonObject = {}
        if email:
            payload["email"] = email
        if linkedin_url:
            payload["linkedinUrl"] = linkedin_url
        if name:
            payload["name"] = name
        if company:
            payload["companyName"] = company
        if not payload:
            return None
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(f"{_BASE}/redeem", headers=self._headers(), json=payload)
        except Exception:
            return None
        if resp.status_code >= 400:
            return None
        body = json_body(resp)
        rec = json_object(body.get("contact")) or body
        return self._normalize(rec) if rec else None

    async def verify_email(self, email: str) -> EmailVerdict:
        return EmailVerdict(email=email, status="unknown")

    async def verify_credentials(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{_BASE}/search", headers=self._headers(), json={"limit": 1}
                )
            return resp.status_code < 400
        except Exception:
            return False
