"""A scripted AgentLLM for deterministic agent-runtime tests (no live API).

Build a list of turns with `text_turn` / `tool_turn` and feed it to `FakeLLM`; the runtime executes
them in order. The linchpin that lets every agent be tested without hitting a real provider.
"""

from collections.abc import AsyncIterator

from app.core.runtime import LLMTurn, Msg, StreamItem, TextDelta, Tool, ToolCall, TurnDone
from app.core.types import JsonObject


def text_turn(text: str, *, in_tok: int = 10, out_tok: int = 20) -> LLMTurn:
    """A final turn — text, no tool calls."""
    return LLMTurn(text=text, tool_calls=[], input_tokens=in_tok, output_tokens=out_tok)


def tool_turn(
    name: str,
    tool_input: JsonObject,
    *,
    call_id: str = "t1",
    in_tok: int = 10,
    out_tok: int = 20,
) -> LLMTurn:
    """A turn that requests one tool call."""
    return LLMTurn(
        text="",
        tool_calls=[ToolCall(id=call_id, name=name, input=tool_input)],
        input_tokens=in_tok,
        output_tokens=out_tok,
    )


class FakeLLM:
    """Returns the scripted turns in order; records the tool names it was shown (for assertions)."""

    def __init__(self, script: list[LLMTurn]) -> None:
        self._script = script
        self.calls = 0
        self.seen_tools: list[list[str]] = []

    async def turn(self, *, system: str, history: list[Msg], tools: list[Tool]) -> LLMTurn:
        self.seen_tools.append([t.name for t in tools])
        turn = self._script[self.calls]
        self.calls += 1
        return turn

    async def stream(
        self, *, system: str, history: list[Msg], tools: list[Tool]
    ) -> AsyncIterator[StreamItem]:
        self.seen_tools.append([t.name for t in tools])
        turn = self._script[self.calls]
        self.calls += 1
        text = turn.text
        mid = len(text) // 2
        for chunk in (text[:mid], text[mid:]):  # a couple of real deltas for tests
            if chunk:
                yield TextDelta(text=chunk)
        yield TurnDone(turn=turn)

    async def complete(self, *, system: str, user: str, max_tokens: int) -> str:
        """The one-off LLM path is exercised through its deterministic fallback, not the FakeLLM;
        this just satisfies the AgentLLM protocol."""
        return ""
