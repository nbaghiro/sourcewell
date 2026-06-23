"""At-rest sealing for stored secrets (provider API keys).

Fernet when a secret is configured (reuses the session cookie key); plaintext fallback for local
dev so the feature works without key management. Either way, the API never returns the raw key.
"""

from cryptography.fernet import Fernet

from app.core.config import get_settings

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
