"""HMAC signing for tamper-proof tokens (unsubscribe links) and inbound webhook verification.

Uses `signing_secret` (or the session cookie key) as the HMAC key, with a dev fallback so links
work locally without key management.
"""

import base64
import hashlib
import hmac

from app.core.config import get_settings

_DEV_FALLBACK = "sourcewell-dev-signing-secret"


def _secret() -> bytes:
    s = get_settings()
    return (s.signing_secret or s.session_cookie_password or _DEV_FALLBACK).encode()


def sign(payload: str) -> str:
    """Return `base64url(payload).mac` — a self-contained signed token."""
    mac = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    body = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    return f"{body}.{mac}"


def verify(token: str) -> str | None:
    """Return the original payload if the token's signature is valid, else None."""
    try:
        body, mac = token.rsplit(".", 1)
    except ValueError:
        return None
    padded = body + "=" * (-len(body) % 4)
    try:
        payload = base64.urlsafe_b64decode(padded).decode()
    except Exception:
        return None
    expected = hmac.new(_secret(), payload.encode(), hashlib.sha256).hexdigest()
    return payload if hmac.compare_digest(expected, mac) else None


def verify_hmac(body: bytes, signature: str | None, *, secret: str | None = None) -> bool:
    """Verify a raw-body HMAC signature (hex sha256), for provider inbound webhooks."""
    if not signature:
        return False
    key = secret.encode() if secret else _secret()
    expected = hmac.new(key, body, hashlib.sha256).hexdigest()
    sig = signature.removeprefix("sha256=").strip()
    return hmac.compare_digest(expected, sig)
