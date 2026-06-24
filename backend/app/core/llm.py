"""Anthropic Claude wrapper (httpx — no SDK dependency).

Key-gated: every call returns None when `anthropic_api_key` is unset or the request fails, so
callers fall back to their deterministic path. The system runs fully without a key; set
ANTHROPIC_API_KEY to turn on real generation.
"""

import json
from typing import TypedDict, cast

import httpx

from app.core.config import get_settings
from app.core.types import JsonObject

_API = "https://api.anthropic.com/v1/messages"
_VERSION = "2023-06-01"
_TIMEOUT = 30.0


class _ContentBlock(TypedDict, total=False):
    """One block of an Anthropic Messages response (we only read text blocks)."""

    type: str
    text: str


class _MessagesResponse(TypedDict, total=False):
    content: list[_ContentBlock]


def is_enabled() -> bool:
    return bool(get_settings().anthropic_api_key)


async def complete(
    system: str, user: str, *, max_tokens: int = 1024, model: str | None = None
) -> str | None:
    """Return Claude's text response, or None if disabled/failed."""
    s = get_settings()
    if not s.anthropic_api_key:
        return None
    payload: JsonObject = {
        "model": model or s.anthropic_model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                _API,
                headers={
                    "x-api-key": s.anthropic_api_key,
                    "anthropic-version": _VERSION,
                    "content-type": "application/json",
                },
                json=payload,
            )
        if resp.status_code >= 400:
            return None
        data = cast(_MessagesResponse, resp.json())
        text = "".join(
            b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
        )
        return text.strip() or None
    except Exception:
        return None


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
