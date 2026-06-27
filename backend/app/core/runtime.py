"""The agent runtime: a bounded, traced, provider-agnostic tool-use loop.

A *run* executes one agent (Strategy / Sourcing / Outreach) against a goal: the model is given a
toolset, and the runtime executes each tool the model requests, feeds the result back, and loops
until the model stops or a guardrail trips (max steps / token budget / timeout). Every run is
persisted as an `AgentRun` + `AgentStep`s — the activity feed + budget trail.

The loop speaks a neutral conversation model (`UserText` / `AssistantTurn` / `ToolResults`); each
provider adapter behind the `AgentLLM` protocol translates that to/from its own wire format. Tests
inject a scripted `FakeLLM` (no live API in CI); the real adapters live in `core/providers.py`.
"""

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.core.types import JsonObject
from app.models import AgentRole, AgentRun, AgentStep

# --- Guardrails (tunable defaults; per the plan) -----------------------------
MAX_STEPS = 12
RUN_TOKEN_BUDGET = 50_000
RUN_TIMEOUT_S = 60.0
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

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        """One-off, no-tools completion (system + user → text) — backs `core/llm.py`."""
        ...


@dataclass
class AgentResult:
    run_id: str
    status: str  # done | error | over_budget | timeout | max_steps
    text: str
    tokens: int
    steps: int


# --- The agent-run loop ------------------------------------------------------


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


async def run_agent(
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
    token_budget: int = RUN_TOKEN_BUDGET,
    timeout_s: float = RUN_TIMEOUT_S,
) -> AgentResult:
    """Execute one bounded, traced agent run. Persists an `AgentRun` + its `AgentStep`s."""
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
    except Exception as exc:  # a tool or the LLM blew up — fail the run, don't crash the worker
        status = "error"
        trace.record("result", None, {"error": str(exc)})

    run.status = status
    run.tokens = tokens
    run.ended_at = datetime.now(UTC)
    run.summary = text[:500]
    await session.flush()
    return AgentResult(run_id=run.id, status=status, text=text, tokens=tokens, steps=trace.seq)


async def stream_agent(
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
    token_budget: int = RUN_TOKEN_BUDGET,
    timeout_s: float = RUN_TIMEOUT_S,
) -> AsyncIterator[JsonObject]:
    """Streaming twin of `run_agent`: yields `{"type":"token","text":...}` as the model emits
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
    except Exception as exc:  # a tool or the stream blew up — end the run cleanly
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


# --- Provider selection ------------------------------------------------------


def default_llm() -> AgentLLM | None:
    """The configured provider's client when its key is set, else None (callers fall back to the
    deterministic path). The adapters + dispatch live in `core/providers.py`."""
    from app.core.providers import build_llm

    return build_llm(get_settings())
