"""The agent runtime: a bounded, traced, provider-agnostic tool-use loop.

An *episode* runs one agent (Strategy / Sourcing / Outreach) against a goal: the model is given a
toolset, and the runtime executes each tool the model requests, feeds the result back, and loops
until the model stops or a guardrail trips (max steps / token budget / timeout). Every episode is
persisted as an `AgentRun` + `AgentStep`s — the activity feed + budget trail.

The loop speaks a neutral conversation model (`UserText` / `AssistantTurn` / `ToolResults`); each
provider adapter behind the `AgentLLM` protocol translates that to/from its own wire format. Tests
inject a scripted `FakeLLM` (no live API in CI); the real adapters live in `core/providers.py`.
"""

import asyncio
import json
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Protocol, cast

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.types import JsonList, JsonObject
from app.models import AgentRole, AgentRun, AgentStep

if TYPE_CHECKING:
    from anthropic.types import MessageParam, ToolParam

# --- Guardrails (tunable defaults; per the plan) -----------------------------
MAX_STEPS = 12
EPISODE_TOKEN_BUDGET = 50_000
EPISODE_TIMEOUT_S = 60.0
CAMPAIGN_DAILY_TOKEN_BUDGET = 500_000


# --- Tool abstraction --------------------------------------------------------


@dataclass(frozen=True)
class Tool:
    """A capability the agent may call. `run` receives the validated input, returns a result.

    `input_schema` is a plain JSON Schema — provider-neutral; each adapter wraps it in its own tool
    format (Anthropic `input_schema`, OpenAI `parameters`, Gemini function declarations).
    """

    name: str
    description: str
    input_schema: JsonObject
    run: Callable[[JsonObject], Awaitable[JsonObject]]


# --- Neutral conversation model (adapters translate this to/from wire format) ----


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: JsonObject


@dataclass(frozen=True)
class UserText:
    """A user / instruction message."""

    text: str


@dataclass(frozen=True)
class AssistantTurn:
    """A prior assistant turn the model produced: its text plus any tool calls it requested."""

    text: str
    tool_calls: list[ToolCall]


@dataclass(frozen=True)
class ToolResults:
    """Results for the tool calls of the immediately preceding assistant turn."""

    results: list[tuple[str, JsonObject]]  # (tool_call_id, result)


Msg = UserText | AssistantTurn | ToolResults


# --- LLM interface (so a scripted FakeLLM can be injected in tests) ----------


@dataclass(frozen=True)
class LLMTurn:
    """One model turn: assistant text + any tool-use requests + token usage."""

    text: str
    tool_calls: list[ToolCall]
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True)
class TextDelta:
    """A streamed chunk of assistant text."""

    text: str


@dataclass(frozen=True)
class TurnDone:
    """Emitted once at the end of a streamed turn — carries the fully assembled turn."""

    turn: LLMTurn


StreamItem = TextDelta | TurnDone


class AgentLLM(Protocol):
    async def turn(self, *, system: str, history: list[Msg], tools: list[Tool]) -> LLMTurn: ...

    def stream(
        self, *, system: str, history: list[Msg], tools: list[Tool]
    ) -> AsyncIterator[StreamItem]: ...


@dataclass
class AgentResult:
    run_id: str
    status: str  # done | error | over_budget | timeout | max_steps
    text: str
    tokens: int
    steps: int


# --- The episode runner ------------------------------------------------------


class _Trace:
    """Accumulates AgentSteps in seq order on the session (flushed with the run)."""

    def __init__(self, session: AsyncSession, run_id: str) -> None:
        self._session = session
        self._run_id = run_id
        self.seq = 0

    def record(self, kind: str, tool_name: str | None, content: JsonObject) -> None:
        self._session.add(
            AgentStep(
                run_id=self._run_id, seq=self.seq, kind=kind, tool_name=tool_name, content=content
            )
        )
        self.seq += 1


def _valid_input(tool: Tool, data: JsonObject) -> bool:
    """Light guardrail: required top-level keys are present (the tool guards the rest)."""
    required = tool.input_schema.get("required")
    if isinstance(required, list):
        return all(isinstance(k, str) and k in data for k in required)
    return True


async def run_episode(
    session: AsyncSession,
    *,
    llm: AgentLLM,
    role: AgentRole,
    trigger: str,
    workspace_id: str,
    system: str,
    user_prompt: str,
    tools: list[Tool],
    campaign_id: str | None = None,
    max_steps: int = MAX_STEPS,
    token_budget: int = EPISODE_TOKEN_BUDGET,
    timeout_s: float = EPISODE_TIMEOUT_S,
) -> AgentResult:
    """Run one bounded, traced agent episode. Persists an `AgentRun` + its `AgentStep`s."""
    run = AgentRun(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        role=role,
        trigger=trigger,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()

    by_name = {t.name: t for t in tools}
    history: list[Msg] = [UserText(user_prompt)]
    trace = _Trace(session, run.id)
    tokens = 0
    status = "max_steps"
    text = ""

    try:
        async with asyncio.timeout(timeout_s):
            for _ in range(max_steps):
                turn = await llm.turn(system=system, history=history, tools=tools)
                tokens += turn.input_tokens + turn.output_tokens
                if turn.text:
                    trace.record("thought", None, {"text": turn.text})
                if not turn.tool_calls:
                    status, text = "done", turn.text
                    break
                history.append(AssistantTurn(turn.text, turn.tool_calls))
                results: list[tuple[str, JsonObject]] = []
                for tc in turn.tool_calls:
                    results.append((tc.id, await _exec_tool(trace, by_name, tc)))
                history.append(ToolResults(results))
                if tokens > token_budget:
                    status = "over_budget"
                    break
    except TimeoutError:
        status = "timeout"
    except Exception as exc:  # a tool or the LLM blew up — fail the episode, don't crash the worker
        status = "error"
        trace.record("result", None, {"error": str(exc)})

    run.status = status
    run.tokens = tokens
    run.ended_at = datetime.now(UTC)
    run.summary = text[:500]
    await session.flush()
    return AgentResult(run_id=run.id, status=status, text=text, tokens=tokens, steps=trace.seq)


async def stream_episode(
    session: AsyncSession,
    *,
    llm: AgentLLM,
    role: AgentRole,
    trigger: str,
    workspace_id: str,
    system: str,
    user_prompt: str,
    tools: list[Tool],
    campaign_id: str | None = None,
    max_steps: int = MAX_STEPS,
    token_budget: int = EPISODE_TOKEN_BUDGET,
    timeout_s: float = EPISODE_TIMEOUT_S,
) -> AsyncIterator[JsonObject]:
    """Streaming twin of `run_episode`: yields `{"type":"token","text":...}` as the model emits
    text, runs any tool calls between turns, and persists the same `AgentRun` + `AgentStep` trace.
    """
    run = AgentRun(
        workspace_id=workspace_id,
        campaign_id=campaign_id,
        role=role,
        trigger=trigger,
        status="running",
        started_at=datetime.now(UTC),
    )
    session.add(run)
    await session.flush()

    by_name = {t.name: t for t in tools}
    history: list[Msg] = [UserText(user_prompt)]
    trace = _Trace(session, run.id)
    tokens = 0
    status = "max_steps"
    text = ""

    try:
        async with asyncio.timeout(timeout_s):
            for _ in range(max_steps):
                turn: LLMTurn | None = None
                async for item in llm.stream(system=system, history=history, tools=tools):
                    if isinstance(item, TextDelta):
                        yield {"type": "token", "text": item.text}
                    else:
                        turn = item.turn
                if turn is None:
                    status = "error"
                    break
                tokens += turn.input_tokens + turn.output_tokens
                if turn.text:
                    trace.record("thought", None, {"text": turn.text})
                if not turn.tool_calls:
                    status, text = "done", turn.text
                    break
                history.append(AssistantTurn(turn.text, turn.tool_calls))
                results: list[tuple[str, JsonObject]] = []
                for tc in turn.tool_calls:
                    results.append((tc.id, await _exec_tool(trace, by_name, tc)))
                history.append(ToolResults(results))
                if tokens > token_budget:
                    status = "over_budget"
                    break
    except TimeoutError:
        status = "timeout"
    except Exception as exc:  # a tool or the stream blew up — end the episode cleanly
        status = "error"
        trace.record("result", None, {"error": str(exc)})

    run.status = status
    run.tokens = tokens
    run.ended_at = datetime.now(UTC)
    run.summary = text[:500]
    await session.flush()


async def _exec_tool(trace: _Trace, by_name: dict[str, Tool], tc: ToolCall) -> JsonObject:
    """Run one tool call (allow-list + light input validation), tracing the call and its result."""
    trace.record("tool_call", tc.name, tc.input)
    tool = by_name.get(tc.name)
    if tool is None:  # allow-list: the model requested an unknown tool
        result: JsonObject = {"error": f"unknown tool: {tc.name}"}
    elif not _valid_input(tool, tc.input):
        result = {"error": f"invalid input for tool: {tc.name}"}
    else:
        try:
            result = await tool.run(tc.input)
        except Exception as exc:
            result = {"error": str(exc)}
    trace.record("result", tc.name, result)
    return result


# --- Real client: the Anthropic SDK ------------------------------------------


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
    """Tool-use client backed by the Anthropic SDK. The SDK is imported lazily so the runtime
    (and FakeLLM-based tests) load without it."""

    def __init__(self, *, api_key: str, model: str, max_tokens: int = 2048) -> None:
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


def default_llm() -> AgentLLM | None:
    """The real client when a key is set, else None (callers fall back to deterministic)."""
    s = get_settings()
    if not s.anthropic_api_key:
        return None
    return AnthropicLLM(api_key=s.anthropic_api_key, model=s.anthropic_model)
