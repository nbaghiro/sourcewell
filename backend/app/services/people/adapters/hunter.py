"""Hunter.io adapter — email finder + verifier (domain-based; no people search). Key-gated."""

import httpx

from app.services.people.adapters.base import (
    EmailVerdict,
    PersonHit,
    ProviderCapabilities,
    SearchPage,
    json_body,
    json_object,
    opt_int,
    opt_str,
)
from app.targeting import Targeting

_BASE = "https://api.hunter.io/v2"
_TIMEOUT = 20.0
_RESULT = {"deliverable": "valid", "risky": "risky", "undeliverable": "invalid"}


class HunterProvider:
    key = "hunter"
    name = "Hunter"
    capabilities = ProviderCapabilities(search=False, enrich=True, verify_email=True)

    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage:
        return SearchPage(hits=[], total=0)  # Hunter has no people-search API

    async def enrich(
        self,
        *,
        email: str | None = None,
        linkedin_url: str | None = None,
        name: str | None = None,
        company: str | None = None,
    ) -> PersonHit | None:
        if not (name and company):
            return None
        parts = name.split()
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{_BASE}/email-finder",
                    params={
                        "api_key": self._key,
                        "company": company,
                        "first_name": parts[0],
                        "last_name": parts[-1],
                    },
                )
        except Exception:
            return None
        if resp.status_code >= 400:
            return None
        data = json_object(json_body(resp).get("data"))
        email = opt_str(data.get("email"))
        if not email:
            return None
        return PersonHit(
            provider=self.key,
            full_name=name,
            company=company,
            email=email,
            email_status="valid" if opt_int(data.get("score")) >= 80 else "risky",
        )

    async def verify_email(self, email: str) -> EmailVerdict:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{_BASE}/email-verifier", params={"api_key": self._key, "email": email}
                )
        except Exception:
            return EmailVerdict(email=email, status="unknown")
        if resp.status_code >= 400:
            return EmailVerdict(email=email, status="unknown")
        data = json_object(json_body(resp).get("data"))
        return EmailVerdict(
            email=email,
            status=_RESULT.get(str(data.get("result")), "unknown"),
            score=opt_int(data.get("score")),
        )

    async def verify_credentials(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{_BASE}/account", params={"api_key": self._key})
            return resp.status_code < 400
        except Exception:
            return False
