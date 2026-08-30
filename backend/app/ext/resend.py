"""Resend adapter — transactional email over HTTPS. Key-gated (blank key → SMTP fallback).

Resend is HTTP-only, so it delivers from hosts that block outbound SMTP ports; the local SMTP
path (Mailpit) stays the fallback so signup is testable offline.
"""

import httpx

from app.core.config import get_settings
from app.core.types import JsonObject

_BASE = "https://api.resend.com"
_TIMEOUT = 20.0


class ResendMailer:
    def __init__(self, api_key: str) -> None:
        self._key = api_key

    async def send(self, *, sender: str, to: str, subject: str, html: str, text: str) -> str | None:
        """Send one message; returns Resend's message id (None if the call failed)."""
        payload: JsonObject = {
            "from": sender,
            "to": [to],
            "subject": subject,
            "html": html,
            "text": text,
        }
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{_BASE}/emails",
                json=payload,
                headers={"Authorization": f"Bearer {self._key}"},
            )
        if resp.status_code >= 400:
            return None
        body = resp.json()
        message_id = body.get("id") if isinstance(body, dict) else None
        return message_id if isinstance(message_id, str) else None


def resend_mailer() -> ResendMailer | None:
    """The configured mailer, or None when no key is set (caller falls back to SMTP)."""
    key = get_settings().resend_api_key
    return ResendMailer(key) if key else None
