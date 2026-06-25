"""Phase 2: the bounded, traced agent tool-use loop + guardrails + the FakeLLM harness."""

import asyncio
from collections.abc import Awaitable, Callable

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents import verticals
from app.core import agent
from app.core.agent import Tool, run_episode
from app.core.config import Settings
from app.core.types import JsonObject
from app.models import AgentRole, AgentRun, AgentStep
from tests.factories import make_org, make_workspace
from tests.fake_llm import FakeLLM, text_turn, tool_turn

# --- dummy tools -------------------------------------------------------------


async def _echo(data: JsonObject) -> JsonObject:
    return {"echo": data}


async def _boom(data: JsonObject) -> JsonObject:
    raise RuntimeError("boom")


async def _slow(data: JsonObject) -> JsonObject:
    await asyncio.sleep(0.3)
    return {}


def _tool(
    name: str,
    run: Callable[[JsonObject], Awaitable[JsonObject]],
    *,
    required: list[str] | None = None,
) -> Tool:
    schema: JsonObject = {"type": "object"}
    if required is not None:
        schema["required"] = required
    return Tool(name=name, description=name, input_schema=schema, run=run)


async def _ws_id(session: AsyncSession, slug: str) -> str:
    org = await make_org(session, slug=slug)
    ws = await make_workspace(session, org=org)
    return ws.id


async def _results(session: AsyncSession, run_id: str) -> list[AgentStep]:
    rows = await session.execute(
        select(AgentStep).where(AgentStep.run_id == run_id, AgentStep.kind == "result")
    )
    return list(rows.scalars().all())


# --- unit: prompt composition + the llm factory ------------------------------


def test_compose_system_layers_base_vertical_context() -> None:
    sysprompt = verticals.compose_system(AgentRole.sourcing, "recruiting", context="Campaign X")
    assert "source" in sysprompt.lower()  # base behavior
    assert "recruiting" in sysprompt.lower()  # vertical overlay
    assert "Campaign X" in sysprompt  # per-episode context


def test_unknown_vertical_falls_back_to_recruiting() -> None:
    assert verticals.get_vertical("aerospace").name == "recruiting"


def test_default_llm_none_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(agent, "get_settings", lambda: Settings(anthropic_api_key=""))
    assert agent.default_llm() is None


def test_default_llm_constructs_with_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        agent, "get_settings", lambda: Settings(anthropic_api_key="sk-x", anthropic_model="m")
    )
    assert agent.default_llm() is not None  # AnthropicLLM constructs (lazy SDK import works)


# --- integration: the episode loop + guardrails + tracing --------------------


@pytest.mark.db
async def test_episode_runs_tool_then_finishes(db_session: AsyncSession) -> None:
    ws = await _ws_id(db_session, "rt-ok")
    llm = FakeLLM([tool_turn("echo", {"x": 1}), text_turn("done")])
    res = await run_episode(
        db_session,
        llm=llm,
        role=AgentRole.sourcing,
        trigger="source_due",
        workspace_id=ws,
        system="s",
        user_prompt="go",
        tools=[_tool("echo", _echo)],
    )
    assert res.status == "done"
    assert res.text == "done"
    assert res.tokens == 60  # two turns * (10 + 20)
    assert res.steps == 3  # tool_call, result, thought

    run = (await db_session.execute(select(AgentRun).where(AgentRun.id == res.run_id))).scalar_one()
    assert run.status == "done"
    assert run.tokens == 60
    assert run.ended_at is not None
    steps = (
        (
            await db_session.execute(
                select(AgentStep).where(AgentStep.run_id == res.run_id).order_by(AgentStep.seq)
            )
        )
        .scalars()
        .all()
    )
    assert [s.kind for s in steps] == ["tool_call", "result", "thought"]
    assert steps[1].content == {"echo": {"x": 1}}


@pytest.mark.db
async def test_unknown_tool_is_rejected(db_session: AsyncSession) -> None:
    ws = await _ws_id(db_session, "rt-unknown")
    llm = FakeLLM([tool_turn("nope", {}), text_turn("ok")])
    res = await run_episode(
        db_session,
        llm=llm,
        role=AgentRole.main,
        trigger="chat",
        workspace_id=ws,
        system="s",
        user_prompt="go",
        tools=[_tool("echo", _echo)],
    )
    assert res.status == "done"  # rejection doesn't crash the episode
    results = await _results(db_session, res.run_id)
    assert any("unknown tool" in str(s.content.get("error", "")) for s in results)


@pytest.mark.db
async def test_invalid_input_is_rejected(db_session: AsyncSession) -> None:
    ws = await _ws_id(db_session, "rt-invalid")
    llm = FakeLLM([tool_turn("search", {}), text_turn("ok")])  # missing required "q"
    res = await run_episode(
        db_session,
        llm=llm,
        role=AgentRole.sourcing,
        trigger="source_due",
        workspace_id=ws,
        system="s",
        user_prompt="go",
        tools=[_tool("search", _echo, required=["q"])],
    )
    assert res.status == "done"
    results = await _results(db_session, res.run_id)
    assert any("invalid input" in str(s.content.get("error", "")) for s in results)


@pytest.mark.db
async def test_tool_exception_is_caught(db_session: AsyncSession) -> None:
    ws = await _ws_id(db_session, "rt-boom")
    llm = FakeLLM([tool_turn("boom", {}), text_turn("recovered")])
    res = await run_episode(
        db_session,
        llm=llm,
        role=AgentRole.sourcing,
        trigger="source_due",
        workspace_id=ws,
        system="s",
        user_prompt="go",
        tools=[_tool("boom", _boom)],
    )
    assert res.status == "done"  # the tool error didn't crash the episode
    results = await _results(db_session, res.run_id)
    assert any("boom" in str(s.content.get("error", "")) for s in results)


@pytest.mark.db
async def test_token_budget_trips(db_session: AsyncSession) -> None:
    ws = await _ws_id(db_session, "rt-budget")
    llm = FakeLLM([tool_turn("echo", {}), text_turn("unreached")])
    res = await run_episode(
        db_session,
        llm=llm,
        role=AgentRole.sourcing,
        trigger="source_due",
        workspace_id=ws,
        system="s",
        user_prompt="go",
        tools=[_tool("echo", _echo)],
        token_budget=20,  # one tool turn (30 tokens) trips it
    )
    assert res.status == "over_budget"
    assert llm.calls == 1  # stopped after the first turn


@pytest.mark.db
async def test_max_steps_trips(db_session: AsyncSession) -> None:
    ws = await _ws_id(db_session, "rt-steps")
    llm = FakeLLM([tool_turn("echo", {}), tool_turn("echo", {}), tool_turn("echo", {})])
    res = await run_episode(
        db_session,
        llm=llm,
        role=AgentRole.sourcing,
        trigger="source_due",
        workspace_id=ws,
        system="s",
        user_prompt="go",
        tools=[_tool("echo", _echo)],
        max_steps=2,
    )
    assert res.status == "max_steps"
    assert llm.calls == 2


@pytest.mark.db
async def test_timeout_trips(db_session: AsyncSession) -> None:
    ws = await _ws_id(db_session, "rt-timeout")
    llm = FakeLLM([tool_turn("slow", {}), text_turn("unreached")])
    res = await run_episode(
        db_session,
        llm=llm,
        role=AgentRole.sourcing,
        trigger="source_due",
        workspace_id=ws,
        system="s",
        user_prompt="go",
        tools=[_tool("slow", _slow)],
        timeout_s=0.05,
    )
    assert res.status == "timeout"
