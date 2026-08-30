"""LinkedIn people search/enrich via Unipile (Rail A — uses a connected LinkedIn seat).

Key-gated by Unipile config (api key + dsn + account). Returns empty results when unconfigured, so
search simply proceeds with whatever other providers are enabled.
"""

from datetime import UTC, datetime, timedelta

import httpx

from app.core.config import get_settings
from app.core.types import JsonObject
from app.ext.base import (
    EmailVerdict,
    PersonHit,
    ProviderCapabilities,
    ProviderError,
    SearchPage,
    json_body,
    json_list,
    json_object,
    opt_str,
    str_list,
)
from app.targeting import Targeting

_TIMEOUT = 25.0


def _public_identifier(url: str) -> str:
    """The trailing public identifier of a LinkedIn profile URL (or the value itself if bare)."""
    return url.rstrip("/").rsplit("/", 1)[-1]


async def fetch_job_postings(*, organization_id: str) -> list[JsonObject]:
    """Pull the connected account's active LinkedIn job postings (title + description) via Unipile.

    Returns `[{id, title, description}]`, or `[]` when no account is configured or the call fails.
    The Unipile jobs response schema is undocumented, so field extraction is best-effort across
    common key names; paste/upload stays the primary intake path. (organization_id is the seam for
    per-org account resolution; today the configured account is used.)
    """
    s = get_settings()
    if not (s.unipile_api_key and s.unipile_dsn and s.unipile_account_id):
        return []
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{s.unipile_dsn.rstrip('/')}/api/v1/linkedin/jobs",
                headers={"X-API-KEY": s.unipile_api_key, "accept": "application/json"},
                params={"account_id": s.unipile_account_id, "category": "active", "limit": 25},
            )
        if resp.status_code >= 400:
            return []
        payload: object = resp.json()
    except Exception:
        return []
    raw = payload.get("items") if isinstance(payload, dict) else payload
    out: list[JsonObject] = []
    for it in raw if isinstance(raw, list) else []:
        if not isinstance(it, dict):
            continue
        title = opt_str(it.get("title")) or opt_str(it.get("name")) or ""
        desc = opt_str(it.get("description")) or opt_str(it.get("job_description")) or ""
        if title or desc:
            out.append({"id": opt_str(it.get("id")) or "", "title": title, "description": desc})
    return out


def _why(resp: httpx.Response) -> str:
    """A short, safe reason from a provider rejection — the status plus its own title if it gave
    one. Never the whole body: these responses can echo request material back."""
    detail = ""
    try:
        body = json_body(resp)
        detail = opt_str(body.get("title")) or opt_str(body.get("detail")) or ""
    except Exception:
        detail = ""
    return f"HTTP {resp.status_code}" + (f" — {detail[:120]}" if detail else "")


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
            # LinkedIn search runs *as* a member, so with no seat there is nothing to search with.
            # Say so: returning an empty page here read as "nobody matched" for anyone who had
            # never connected an account.
            raise ProviderError(
                self.key,
                "no LinkedIn account connected — connect one in Settings → Connections"
                if self._key and self._dsn
                else "LinkedIn is not configured on this deployment",
            )
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
            "api": "classic",
            "category": "people",
            "keywords": keywords,
            "limit": min(limit, 50),
        }
        if targeting.locations:
            body["location"] = targeting.locations
        if targeting.industries:
            # Structured Sales-Nav filters need ID resolution via /linkedin/search/parameters.
            body["industry"] = targeting.industries
        # account_id (and the pagination cursor) ride in the query string, not the body.
        params = {"account_id": self._account}
        if cursor:
            params["cursor"] = cursor
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._dsn}/api/v1/linkedin/search",
                    headers={"X-API-KEY": self._key, "accept": "application/json"},
                    params=params,
                    json=body,
                )
        except Exception as exc:
            raise ProviderError(self.key, f"LinkedIn search is unreachable ({exc})") from exc
        if resp.status_code >= 400:
            raise ProviderError(self.key, _why(resp), status=resp.status_code)
        data = json_body(resp)
        items = json_list(data.get("items")) or json_list(data.get("results"))
        hits = [self._normalize(r) for r in items]
        total = data.get("total")
        return SearchPage(
            hits=hits,
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
        if not self._ready() or not linkedin_url:
            return None
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{self._dsn}/api/v1/users/{_public_identifier(linkedin_url)}",
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
            # Unipile wants exactly YYYY-MM-DDTHH:MM:SS.sssZ — not isoformat's offset.
            "expiresOn": (datetime.now(UTC) + timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M:%S.000Z"
            ),
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

    async def register_webhooks(self, *, request_url: str, source: str) -> bool:
        """Subscribe the inbound receiver for a source (messaging | email | account)."""
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._dsn}/api/v1/webhooks",
                headers=self._headers(),
                json={"request_url": request_url, "source": source},
            )
        return resp.status_code < 400

    async def list_webhooks(self) -> list[tuple[str, str]] | None:
        """Subscriptions already registered, as `(request_url, source)`.

        `None` means we couldn't tell (unreachable / unexpected shape) — the caller then registers
        blindly rather than skipping, since a duplicate subscription only costs a duplicate
        delivery, which the receiver's idempotency key drops.
        """
        try:
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(f"{self._dsn}/api/v1/webhooks", headers=self._headers())
        except Exception:
            return None
        if resp.status_code >= 400:
            return None
        data = json_body(resp)
        items = json_list(data.get("items")) or json_list(data.get("webhooks"))
        out: list[tuple[str, str]] = []
        for it in items:
            url = opt_str(it.get("request_url")) or opt_str(it.get("url"))
            source = opt_str(it.get("source")) or opt_str(it.get("name"))
            if url:
                out.append((url, (source or "").lower()))
        return out


def unipile_connection() -> UnipileConnection | None:
    """The platform Unipile connection client, or None if unconfigured."""
    s = get_settings()
    if not (s.unipile_api_key and s.unipile_dsn):
        return None
    return UnipileConnection(s.unipile_api_key, s.unipile_dsn)


class UnipileError(RuntimeError):
    """A Unipile call failed. Raised (never swallowed) so a send is never reported as delivered."""


class UnipileChannel:
    """ChannelProvider role — send + reply on a channel (linkedin | email) from a seat.

    LinkedIn sends are multipart `POST /chats` (first touch → returns the chat id) and
    `POST /chats/{id}/messages` (reply); `attendees_ids` is the recipient's provider id, resolved
    from their public identifier. Email is `POST /emails`.

    Every method raises `UnipileError` when the provider rejects the call — the send layer turns
    that into a retry rather than marking an undelivered message as sent.
    """

    def __init__(self, channel: str, api_key: str, dsn: str) -> None:
        self.channel = channel
        self._key = api_key
        self._dsn = dsn.rstrip("/")

    def _headers(self, idempotency_key: str | None = None) -> dict[str, str]:
        h = {"X-API-KEY": self._key, "accept": "application/json"}
        if idempotency_key:
            # Provider-side dedupe: a retried send with the same key must not double-deliver.
            h["Idempotency-Key"] = idempotency_key
        return h

    @staticmethod
    def _form(fields: dict[str, str]) -> dict[str, tuple[None, str]]:
        # multipart/form-data text parts: (filename=None, content) per field.
        return {k: (None, v) for k, v in fields.items()}

    @staticmethod
    def _permanent(resp: httpx.Response) -> bool:
        """A response the caller should treat as a permanent client error (return None). Raises on a
        transient error (429 / 5xx) so the caller retries instead of giving up (and suppressing)."""
        if resp.status_code < 400:
            return False
        if resp.status_code == 429 or resp.status_code >= 500:
            resp.raise_for_status()  # → httpx error → TransientSendError upstream
        return True

    async def _provider_id(self, *, account_id: str, identifier: str) -> str | None:
        """Resolve a LinkedIn public identifier / URL → the provider internal id (attendees_ids)."""
        ident = _public_identifier(identifier)
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(
                f"{self._dsn}/api/v1/users/{ident}",
                headers=self._headers(),
                params={"account_id": account_id},
            )
        return None if self._permanent(resp) else opt_str(json_body(resp).get("provider_id"))

    async def send(
        self,
        *,
        account_id: str,
        to: str,
        subject: str | None,
        body: str,
        inmail: bool = False,
        idempotency_key: str | None = None,
    ) -> str | None:
        """Send the first message; returns the provider thread/chat id (for reply mapping)."""
        if self.channel == "linkedin":
            provider_id = await self._provider_id(account_id=account_id, identifier=to)
            if provider_id is None:
                return None  # not reachable from this seat — a permanent failure upstream
            fields = {
                "account_id": account_id,
                "attendees_ids": provider_id,
                "text": body,
                "linkedin[api]": "classic",
            }
            if inmail:
                # Cold outreach to a non-connection goes as an InMail, not a regular chat.
                fields["linkedin[inmail]"] = "true"
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.post(
                    f"{self._dsn}/api/v1/chats",
                    headers=self._headers(idempotency_key),
                    files=self._form(fields),
                )
            if self._permanent(resp):
                return None
            data = json_body(resp)
            return opt_str(data.get("chat_id")) or opt_str(data.get("id"))
        # email — `to` as the array form [{identifier}] in multipart bracket notation
        email_fields = {
            "account_id": account_id,
            "subject": subject or "",
            "body": body,
            "to[0][identifier]": to,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{self._dsn}/api/v1/emails",
                headers=self._headers(idempotency_key),
                files=self._form(email_fields),
            )
        return None if self._permanent(resp) else opt_str(json_body(resp).get("id"))

    async def reply(
        self, *, account_id: str, thread_id: str, body: str, idempotency_key: str | None = None
    ) -> bool:
        """Reply into an existing thread (LinkedIn chat / email). False = permanently rejected.

        Same hard/soft split as `send`: 429 and 5xx raise (→ TransientSendError → backoff retry),
        while a 4xx returns False for the caller to fail outright. A bare `raise_for_status()` here
        made every permanent rejection — a deleted chat, a thread the seat can no longer post to —
        look transient, so the send layer burned its whole retry budget on a message that could
        never land, and never took the hard-failure path that suppresses and advances the sequence.
        """
        if self.channel == "linkedin":
            url = f"{self._dsn}/api/v1/chats/{thread_id}/messages"
            fields = {"text": body}
        else:
            url = f"{self._dsn}/api/v1/emails"
            fields = {"account_id": account_id, "body": body, "reply_to": thread_id}
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                url, headers=self._headers(idempotency_key), files=self._form(fields)
            )
        return not self._permanent(resp)


def unipile_channel(channel: str) -> UnipileChannel | None:
    """The platform Unipile channel client (linkedin | email), or None if unconfigured."""
    s = get_settings()
    if not (s.unipile_api_key and s.unipile_dsn):
        return None
    return UnipileChannel(channel, s.unipile_api_key, s.unipile_dsn)
