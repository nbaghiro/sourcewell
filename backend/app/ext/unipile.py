"""LinkedIn people search/enrich via Unipile (Rail A — uses a connected LinkedIn seat).

Key-gated by Unipile config (api key + dsn + account). Returns empty results when unconfigured, so
the registry simply falls back to other providers / the demo provider.
"""

from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import get_settings
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

_TIMEOUT = 25.0


async def fetch_job_postings(*, organization_id: str) -> list[str]:
    """Pull the org's open job postings from its connected LinkedIn account (Unipile) as JD text.

    STUB: the real implementation will hit Unipile's recruiter/job endpoints for the org's account.
    Returns `[]` for now so the intake flow's "pull from LinkedIn" source is wired end-to-end;
    paste/upload is the working path until this lands.
    """
    return []


class UnipileProvider:
    key = "linkedin"
    name = "LinkedIn (Unipile)"
    capabilities = ProviderCapabilities(search=True, enrich=True, verify_email=False)

    def __init__(self, api_key: str, account_id: str | None = None) -> None:
        s = get_settings()
        self._key = api_key
        self._dsn = s.unipile_dsn.rstrip("/")
        # Per-seat account when resolved from a Connection; settings is the back-compat fallback.
        self._account = account_id or s.unipile_account_id

    def _ready(self) -> bool:
        return bool(self._key and self._dsn and self._account)

    def _normalize(self, rec: JsonObject) -> PersonHit:
        name = opt_str(rec.get("name")) or " ".join(
            filter(None, [opt_str(rec.get("first_name")), opt_str(rec.get("last_name"))])
        )
        company = opt_str(rec.get("company"))
        if not company:
            company = opt_str(json_object(rec.get("current_company")).get("name"))
        return PersonHit(
            provider=self.key,
            external_id=opt_str(rec.get("id")) or opt_str(rec.get("public_identifier")),
            full_name=name or "",
            title=opt_str(rec.get("headline")) or opt_str(rec.get("title")),
            company=company,
            location=opt_str(rec.get("location")),
            linkedin_url=opt_str(rec.get("profile_url")) or opt_str(rec.get("public_profile_url")),
            skills=str_list(rec.get("skills"), 12),
        )

    async def search(
        self, targeting: Targeting, *, limit: int = 25, cursor: str | None = None
    ) -> SearchPage:
        if not self._ready():
            return SearchPage(hits=[], total=0)
        # Unipile LinkedIn search is keyword-based; fold the scorable/search facets we can express
        # (titles, skills, companies, technologies, seniorities, free text) into the keyword string.
        keywords = " ".join(
            [
                *targeting.titles,
                *targeting.skills,
                *targeting.companies,
                *targeting.technologies,
                *targeting.seniorities,
                *([targeting.keywords] if targeting.keywords else []),
            ]
        ).strip()
        body: JsonObject = {
            "account_id": self._account,
            "keywords": keywords,
            "limit": min(limit, 50),
        }
        if targeting.locations:
            body["location"] = targeting.locations
        if targeting.industries:
            body["industry"] = targeting.industries  # TODO: confirm Unipile filter key/format
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._dsn}/api/v1/linkedin/search",
                headers={"X-API-KEY": self._key, "accept": "application/json"},
                json=body,
            )
        if resp.status_code >= 400:
            return SearchPage(hits=[], total=0)
        data = json_body(resp)
        items = json_list(data.get("items")) or json_list(data.get("results"))
        hits = [self._normalize(r) for r in items]
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
        if not self._ready() or not linkedin_url:
            return None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{self._dsn}/api/v1/users/{linkedin_url}",
                headers={"X-API-KEY": self._key, "accept": "application/json"},
                params={"account_id": self._account},
            )
        if resp.status_code >= 400:
            return None
        rec = json_body(resp)
        return self._normalize(rec) if rec else None

    async def verify_email(self, email: str) -> EmailVerdict:
        return EmailVerdict(email=email, status="unknown")

    async def verify_credentials(self) -> bool:
        if not (self._key and self._dsn):
            return False
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(
                    f"{self._dsn}/api/v1/accounts",
                    headers={"X-API-KEY": self._key, "accept": "application/json"},
                )
            return resp.status_code < 400
        except Exception:
            return False


class UnipileConnection:
    """ConnectionProvider role — connect seats (hosted auth), read identity, register webhooks."""

    def __init__(self, api_key: str, dsn: str) -> None:
        self._key = api_key
        self._dsn = dsn.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {"X-API-KEY": self._key, "accept": "application/json"}

    async def create_link(self, *, user_ref: str, notify_url: str, redirect_url: str) -> str | None:
        """Create a hosted-auth wizard link to connect a LinkedIn seat. Returns the URL."""
        body: JsonObject = {
            "type": "create",
            "providers": ["LINKEDIN"],
            "api_url": self._dsn,
            "expiresOn": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
            "notify_url": notify_url,
            "success_redirect_url": redirect_url,
            "name": user_ref,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._dsn}/api/v1/hosted/accounts/link", headers=self._headers(), json=body
            )
        return opt_str(json_body(resp).get("url")) if resp.status_code < 400 else None

    async def profile(self, *, account_id: str) -> JsonObject | None:
        """Read the connected account's own profile (identity incl. `member_urn`)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{self._dsn}/api/v1/users/me",
                headers=self._headers(),
                params={"account_id": account_id},
            )
        return json_body(resp) if resp.status_code < 400 else None

    async def register_webhooks(self, *, request_url: str, source: str) -> None:
        """Subscribe the inbound receiver for a source (messaging | email | account)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            await client.post(
                f"{self._dsn}/api/v1/webhooks",
                headers=self._headers(),
                json={"request_url": request_url, "source": source},
            )


def unipile_connection() -> UnipileConnection | None:
    """The platform Unipile connection client, or None if unconfigured."""
    s = get_settings()
    if not (s.unipile_api_key and s.unipile_dsn):
        return None
    return UnipileConnection(s.unipile_api_key, s.unipile_dsn)
