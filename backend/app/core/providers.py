"""Provider adapters for the agent runtime — one per LLM vendor, each with its native SDK.

The runtime (`core/runtime.py`) speaks a neutral conversation model (`UserText` / `AssistantTurn` /
`ToolResults`). Every adapter here translates that to/from its vendor's wire format and parses the
response back into a neutral `LLMTurn`. SDKs are imported lazily so the runtime + FakeLLM tests
load without any of them installed. `build_llm()` selects the adapter from settings, and
`runtime.default_llm` is the public entry point that calls it.

Providers use their native SDKs: Anthropic (`anthropic`), OpenAI (`openai`), Gemini
(`google-genai`), and xAI (the `openai` SDK pointed at xAI's base URL — xAI's recommended client).
Streaming is real for Anthropic; OpenAI/Gemini fall back to a single-shot turn surfaced as one
delta (token-level streaming for them is a follow-up).
"""

import json
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, cast

from app.core.config import Settings
from app.core.runtime import (
    AgentLLM,
    AssistantTurn,
    LLMTurn,
    Msg,
    StreamItem,
    TextDelta,
    Tool,
    ToolCall,
    TurnDone,
    UserText,
)
from app.core.types import JsonList, JsonObject

if TYPE_CHECKING:
    from anthropic.types import MessageParam, ToolParam
    from openai.types.chat import ChatCompletionMessageParam, ChatCompletionToolParam

_DEFAULT_MODELS = {"openai": "gpt-4o", "xai": "grok-2-latest", "gemini": "gemini-2.0-flash"}
_MAX_TOKENS = 2048


# === Anthropic ===============================================================


def _to_anthropic_messages(history: list[Msg]) -> JsonList:
    """Translate the neutral history into Anthropic Messages wire format."""
    msgs: JsonList = []
    for m in history:
        if isinstance(m, UserText):
            msgs.append({"role": "user", "content": m.text})
        elif isinstance(m, AssistantTurn):
            blocks: JsonList = []
            if m.text:
                blocks.append({"type": "text", "text": m.text})
            for tc in m.tool_calls:
                blocks.append({"type": "tool_use", "id": tc.id, "name": tc.name, "input": tc.input})
            msgs.append({"role": "assistant", "content": blocks})
        else:  # ToolResults
            msgs.append(
                {
                    "role": "user",
                    "content": [
                        {"type": "tool_result", "tool_use_id": cid, "content": json.dumps(res)}
                        for cid, res in m.results
                    ],
                }
            )
    return msgs


def _to_anthropic_tools(tools: list[Tool]) -> JsonList:
    return [
        {"name": t.name, "description": t.description, "input_schema": t.input_schema}
        for t in tools
    ]


class AnthropicLLM:
    """Tool-use client backed by the Anthropic SDK (imported lazily)."""

    def __init__(self, *, api_key: str, model: str, max_tokens: int = _MAX_TOKENS) -> None:
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def turn(self, *, system: str, history: list[Msg], tools: list[Tool]) -> LLMTurn:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=cast("list[MessageParam]", _to_anthropic_messages(history)),
            tools=cast("list[ToolParam]", _to_anthropic_tools(tools)),
        )
        text = ""
        tool_calls: list[ToolCall] = []
        for block in resp.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                raw = block.input
                inp: JsonObject = (
                    {str(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}
                )
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=inp))
        return LLMTurn(
            text=text,
            tool_calls=tool_calls,
            input_tokens=resp.usage.input_tokens,
            output_tokens=resp.usage.output_tokens,
        )

    async def stream(
        self, *, system: str, history: list[Msg], tools: list[Tool]
    ) -> AsyncIterator[StreamItem]:
        async with self._client.messages.stream(
            model=self._model,
            max_tokens=self._max_tokens,
            system=system,
            messages=cast("list[MessageParam]", _to_anthropic_messages(history)),
            tools=cast("list[ToolParam]", _to_anthropic_tools(tools)),
        ) as stream:
            async for event in stream:
                if event.type == "text":  # the SDK's high-level text-delta event
                    yield TextDelta(text=event.text)
            final = await stream.get_final_message()
        text = ""
        tool_calls: list[ToolCall] = []
        for block in final.content:
            if block.type == "text":
                text += block.text
            elif block.type == "tool_use":
                raw = block.input
                inp: JsonObject = (
                    {str(k): v for k, v in raw.items()} if isinstance(raw, dict) else {}
                )
                tool_calls.append(ToolCall(id=block.id, name=block.name, input=inp))
        yield TurnDone(
            turn=LLMTurn(
                text=text,
                tool_calls=tool_calls,
                input_tokens=final.usage.input_tokens,
                output_tokens=final.usage.output_tokens,
            )
        )

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        resp = await self._client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=cast("list[MessageParam]", [{"role": "user", "content": user}]),
        )
        return "".join(b.text for b in resp.content if b.type == "text")


# === OpenAI / xAI ============================================================


def _to_openai_messages(system: str, history: list[Msg]) -> JsonList:
    """Translate the neutral history into OpenAI Chat Completions wire format."""
    msgs: JsonList = [{"role": "system", "content": system}]
    for m in history:
        if isinstance(m, UserText):
            msgs.append({"role": "user", "content": m.text})
        elif isinstance(m, AssistantTurn):
            msg: JsonObject = {"role": "assistant", "content": m.text or None}
            if m.tool_calls:
                msg["tool_calls"] = [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.input)},
                    }
                    for tc in m.tool_calls
                ]
            msgs.append(msg)
        else:  # ToolResults
            for cid, res in m.results:
                msgs.append({"role": "tool", "tool_call_id": cid, "content": json.dumps(res)})
    return msgs


def _to_openai_tools(tools: list[Tool]) -> JsonList:
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.input_schema,
            },
        }
        for t in tools
    ]


class OpenAILLM:
    """Chat-completions client for OpenAI (and xAI via base_url — xAI's recommended client)."""

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        base_url: str | None = None,
        max_tokens: int = _MAX_TOKENS,
    ) -> None:
        from openai import AsyncOpenAI

        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url)
        self._model = model
        self._max_tokens = max_tokens

    async def turn(self, *, system: str, history: list[Msg], tools: list[Tool]) -> LLMTurn:
        resp = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=self._max_tokens,
            messages=cast("list[ChatCompletionMessageParam]", _to_openai_messages(system, history)),
            tools=cast("list[ChatCompletionToolParam]", _to_openai_tools(tools)),
        )
        choice = resp.choices[0].message
        text = choice.content or ""
        tool_calls: list[ToolCall] = []
        for tc in choice.tool_calls or []:
            if tc.type != "function":  # ignore custom/non-function tool calls
                continue
            try:
                parsed: object = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                parsed = {}
            inp: JsonObject = (
                {str(k): v for k, v in parsed.items()} if isinstance(parsed, dict) else {}
            )
            tool_calls.append(ToolCall(id=tc.id, name=tc.function.name, input=inp))
        usage = resp.usage
        return LLMTurn(
            text=text,
            tool_calls=tool_calls,
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    async def stream(
        self, *, system: str, history: list[Msg], tools: list[Tool]
    ) -> AsyncIterator[StreamItem]:
        turn = await self.turn(system=system, history=history, tools=tools)
        if turn.text:
            yield TextDelta(text=turn.text)
        yield TurnDone(turn=turn)

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        resp = await self._client.chat.completions.create(
            model=self._model,
            max_tokens=max_tokens,
            messages=cast(
                "list[ChatCompletionMessageParam]",
                [{"role": "system", "content": system}, {"role": "user", "content": user}],
            ),
        )
        return resp.choices[0].message.content or ""


# === Gemini ==================================================================


class GeminiLLM:
    """Tool-use client backed by the Google GenAI SDK (`google-genai`, imported lazily)."""

    def __init__(self, *, api_key: str, model: str, max_tokens: int = _MAX_TOKENS) -> None:
        from google import genai

        self._client = genai.Client(api_key=api_key)
        self._model = model
        self._max_tokens = max_tokens

    async def turn(self, *, system: str, history: list[Msg], tools: list[Tool]) -> LLMTurn:
        from google.genai import types

        id_to_name: dict[str, str] = {}
        contents: list[types.Content] = []
        for m in history:
            if isinstance(m, UserText):
                contents.append(types.Content(role="user", parts=[types.Part(text=m.text)]))
            elif isinstance(m, AssistantTurn):
                parts: list[types.Part] = []
                if m.text:
                    parts.append(types.Part(text=m.text))
                for tc in m.tool_calls:
                    id_to_name[tc.id] = tc.name
                    parts.append(
                        types.Part(
                            function_call=types.FunctionCall(id=tc.id, name=tc.name, args=tc.input)
                        )
                    )
                contents.append(types.Content(role="model", parts=parts))
            else:  # ToolResults
                contents.append(
                    types.Content(
                        role="user",
                        parts=[
                            types.Part(
                                function_response=types.FunctionResponse(
                                    id=cid, name=id_to_name.get(cid, ""), response={"result": res}
                                )
                            )
                            for cid, res in m.results
                        ],
                    )
                )
        decls = [
            types.FunctionDeclaration(
                name=t.name, description=t.description, parameters_json_schema=t.input_schema
            )
            for t in tools
        ]
        config = types.GenerateContentConfig(
            system_instruction=system,
            tools=[types.Tool(function_declarations=decls)],
            max_output_tokens=self._max_tokens,
        )
        resp = await self._client.aio.models.generate_content(
            model=self._model, contents=contents, config=config
        )
        text = ""
        tool_calls: list[ToolCall] = []
        cand = resp.candidates[0] if resp.candidates else None
        parts_out = cand.content.parts if cand and cand.content and cand.content.parts else []
        for i, part in enumerate(parts_out):
            if part.text:
                text += part.text
            fc = part.function_call
            if fc is not None:
                inp: JsonObject = {str(k): v for k, v in fc.args.items()} if fc.args else {}
                tool_calls.append(ToolCall(id=fc.id or f"call_{i}", name=fc.name or "", input=inp))
        um = resp.usage_metadata
        return LLMTurn(
            text=text,
            tool_calls=tool_calls,
            input_tokens=(um.prompt_token_count or 0) if um else 0,
            output_tokens=(um.candidates_token_count or 0) if um else 0,
        )

    async def stream(
        self, *, system: str, history: list[Msg], tools: list[Tool]
    ) -> AsyncIterator[StreamItem]:
        turn = await self.turn(system=system, history=history, tools=tools)
        if turn.text:
            yield TextDelta(text=turn.text)
        yield TurnDone(turn=turn)

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        from google.genai import types

        resp = await self._client.aio.models.generate_content(
            model=self._model,
            contents=[types.Content(role="user", parts=[types.Part(text=user)])],
            config=types.GenerateContentConfig(
                system_instruction=system, max_output_tokens=max_tokens
            ),
        )
        cand = resp.candidates[0] if resp.candidates else None
        parts = cand.content.parts if cand and cand.content and cand.content.parts else []
        return "".join(p.text for p in parts if p.text)


# === Dispatch ================================================================


def _provider_key(s: Settings) -> str | None:
    """The API key for the configured provider, or None if unset / the provider is unknown."""
    keys = {
        "anthropic": s.anthropic_api_key,
        "openai": s.openai_api_key,
        "xai": s.xai_api_key,
        "gemini": s.gemini_api_key,
    }
    return keys.get((s.agent_provider or "anthropic").lower()) or None


def provider_ready(s: Settings) -> bool:
    """Whether the configured provider has a key — the cheap gate for `core/llm.py:is_enabled`."""
    return _provider_key(s) is not None


def build_llm(s: Settings) -> AgentLLM | None:
    """Construct the configured provider's client, or None when its key is unset (callers then fall
    back to their deterministic path). One provider + one model — no per-task tiering yet."""
    provider = (s.agent_provider or "anthropic").lower()
    key = _provider_key(s)
    if key is None:
        return None
    if provider == "anthropic":
        return AnthropicLLM(api_key=key, model=s.agent_model or s.anthropic_model)
    if provider == "openai":
        return OpenAILLM(api_key=key, model=s.agent_model or _DEFAULT_MODELS["openai"])
    if provider == "xai":
        return OpenAILLM(
            api_key=key,
            model=s.agent_model or _DEFAULT_MODELS["xai"],
            base_url="https://api.x.ai/v1",
        )
    if provider == "gemini":
        return GeminiLLM(api_key=key, model=s.agent_model or _DEFAULT_MODELS["gemini"])
    return None


async def complete_text(s: Settings, *, system: str, user: str, max_tokens: int) -> str | None:
    """One-off, no-tools completion through the configured provider — backs `core/llm.py`. Returns
    None when no provider is configured or the call fails, so callers fall back to deterministic."""
    client = build_llm(s)
    if client is None:
        return None
    try:
        text = await client.complete(system=system, user=user, max_tokens=max_tokens)
    except Exception:
        return None
    return text.strip() or None
