"""Provider-agnostic one-off LLM completions (system + user → text / JSON).

Routes through the configured provider in `core/providers.py` — the same client classes the agent
runtime uses, so switching `AGENT_PROVIDER` switches drafting/intake/scoring too. Key-gated: every
call returns None when no provider key is set or the request fails, so callers fall back to their
deterministic path. The system runs fully without a key; configure a provider to turn on generation.
"""

import json

from app.core.config import get_settings
from app.core.types import JsonObject


def is_enabled() -> bool:
    """True when the configured provider has a key set."""
    from app.core.providers import provider_ready

    return provider_ready(get_settings())


async def complete(system: str, user: str, *, max_tokens: int = 1024) -> str | None:
    """Return the model's text response, or None if no provider is configured / the call failed."""
    from app.core.providers import complete_text

    return await complete_text(get_settings(), system=system, user=user, max_tokens=max_tokens)


async def complete_json(system: str, user: str, *, max_tokens: int = 1024) -> JsonObject | None:
    """Like `complete`, but parse a JSON object from the response."""
    text = await complete(
        f"{system}\nRespond with ONLY a valid JSON object, no prose or code fences.",
        user,
        max_tokens=max_tokens,
    )
    if text is None:
        return None
    cleaned = text.strip().removeprefix("```json").removeprefix("```").removesuffix("```").strip()
    try:
        obj: object = json.loads(cleaned)
    except Exception:
        return None
    return {str(k): v for k, v in obj.items()} if isinstance(obj, dict) else None
