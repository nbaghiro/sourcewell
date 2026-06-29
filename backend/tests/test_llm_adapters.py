"""LLM provider adapters — the neutral↔wire translation, as pure functions (no live SDK).

These are the riskiest new code (Anthropic / OpenAI message + tool shaping) and were previously only
reachable via a real API call. The translation is deterministic, so we assert the wire format here:
roles, the tool_use ↔ tool_result id correlation, and the tool-spec shape per provider.
"""

import json

from app.core.providers import (
    _to_anthropic_messages,
    _to_anthropic_tools,
    _to_openai_messages,
    _to_openai_tools,
)
from app.core.runtime import AssistantTurn, Msg, Tool, ToolCall, ToolResults, UserText


def _as_list(v: object) -> list[object]:
    assert isinstance(v, list)
    return v


def _as_dict(v: object) -> dict[str, object]:
    assert isinstance(v, dict)
    return v


def _history() -> list[Msg]:
    return [
        UserText("find me VPs"),
        AssistantTurn("on it", [ToolCall(id="c1", name="search", input={"q": "VP"})]),
        ToolResults([("c1", {"found": 3})]),
    ]


async def _noop(_: dict[str, object]) -> dict[str, object]:
    return {}


def _tools() -> list[Tool]:
    return [Tool(name="search", description="Search.", input_schema={"type": "object"}, run=_noop)]


def test_anthropic_messages_correlate_tool_use_and_result() -> None:
    msgs = _to_anthropic_messages(_history())
    assert msgs[0] == {"role": "user", "content": "find me VPs"}

    asst = msgs[1]
    assert asst["role"] == "assistant"
    blocks = _as_list(asst["content"])
    assert {"type": "text", "text": "on it"} in blocks
    assert {"type": "tool_use", "id": "c1", "name": "search", "input": {"q": "VP"}} in blocks

    result = msgs[2]
    assert result["role"] == "user"
    block = _as_dict(_as_list(result["content"])[0])
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "c1"  # must reference the assistant turn's tool_use id
    assert block["content"] == json.dumps({"found": 3})


def test_anthropic_tools_shape() -> None:
    assert _to_anthropic_tools(_tools()) == [
        {"name": "search", "description": "Search.", "input_schema": {"type": "object"}}
    ]


def test_openai_messages_correlate_tool_call_and_tool_message() -> None:
    msgs = _to_openai_messages("be helpful", _history())
    assert msgs[0] == {"role": "system", "content": "be helpful"}
    assert msgs[1] == {"role": "user", "content": "find me VPs"}

    asst = msgs[2]
    assert asst["role"] == "assistant"
    assert asst["content"] == "on it"
    call = _as_dict(_as_list(asst["tool_calls"])[0])
    assert call["id"] == "c1"
    assert call["type"] == "function"
    fn = _as_dict(call["function"])
    assert fn["name"] == "search"
    assert fn["arguments"] == json.dumps({"q": "VP"})

    result = msgs[3]
    assert result["role"] == "tool"
    assert result["tool_call_id"] == "c1"  # must reference the assistant turn's tool_call id
    assert result["content"] == json.dumps({"found": 3})


def test_openai_tools_shape() -> None:
    specs = _to_openai_tools(_tools())
    fn = _as_dict(_as_dict(specs[0])["function"])
    assert _as_dict(specs[0])["type"] == "function"
    assert fn["name"] == "search"
    assert fn["parameters"] == {"type": "object"}
