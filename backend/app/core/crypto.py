"""Cryptographic helpers: at-rest secret sealing + HMAC-signed tokens.

- **Sealing** (`seal`/`unseal`): Fernet when a secret is configured (reuses the session cookie
  key); plaintext fallback for local dev. Used for stored provider API keys — never returned raw.
- **Signing** (`sign`/`verify`/`verify_hmac`): tamper-proof tokens (unsubscribe links) + inbound
  webhook verification, keyed by `signing_secret` (or the session cookie key) with a dev fallback.
- **Passwords** (`hash_password`/`verify_password`): scrypt at rest for the email/password login.
"""

import base64
import hashlib
import hmac
import os

from cryptography.fernet import Fernet

from app.core.config import get_settings

# --- At-rest sealing (Fernet) ------------------------------------------------

_PLAIN = "plain:"
_ENC = "enc:"


def _fernets() -> list[Fernet]:
    """Active key first, then any previous key (for rotation): seal with [0], decrypt with any."""
    s = get_settings()
    keys = [k for k in (s.session_cookie_password, s.session_cookie_password_previous) if k]
    return [Fernet(k.encode()) for k in keys]


def seal(value: str) -> str:
    fernets = _fernets()
    if not fernets:
        return _PLAIN + value
    return _ENC + fernets[0].encrypt(value.encode()).decode()


def unseal(token: str) -> str:
    if token.startswith(_ENC):
        body = token[len(_ENC) :].encode()
        for fernet in _fernets():
            try:
                return fernet.decrypt(body).decode()
            except Exception:
                continue
        raise RuntimeError("cannot decrypt secret: no matching key configured")
    if token.startswith(_PLAIN):
        return token[len(_PLAIN) :]
    return token


# --- HMAC signing ------------------------------------------------------------

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


# --- Password hashing (scrypt; stdlib, no external dep) ----------------------

_SCRYPT = {"n": 2**14, "r": 8, "p": 1, "maxmem": 2**26, "dklen": 32}


def hash_password(password: str) -> str:
    """scrypt hash, stored as `scrypt$<salt_hex>$<hash_hex>`."""
    salt = os.urandom(16)
    dk = hashlib.scrypt(password.encode(), salt=salt, **_SCRYPT)
    return f"scrypt${salt.hex()}${dk.hex()}"


def verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 3 or parts[0] != "scrypt":
        return False
    dk = hashlib.scrypt(password.encode(), salt=bytes.fromhex(parts[1]), **_SCRYPT)
    return hmac.compare_digest(dk.hex(), parts[2])
