"""DoD #1 — ReAct loop drives one round end-to-end with a mock LLM.

Expected ordering for one tool-then-content scenario:
    thinking -> tool_call -> tool_result -> content -> done
"""
from __future__ import annotations

import pytest

from app.agent_engine.engine import ReActLoopImpl
from app.agent_engine.llm import ChatMessage, LLMResponse, ToolCallSpec
from app.agent_engine.react import AgentDefinition
from app.agent_engine.registry import InMemoryToolRegistry
from tests._seams.echo_tool import EchoTool
from tests._seams.inmemory_storage import InMemoryStorage
from tests._seams.mock_llm import ScriptedLLM


def _build_loop(storage: InMemoryStorage, *, clock, id_gen) -> tuple[ReActLoopImpl, ScriptedLLM]:
    registry = InMemoryToolRegistry()
    registry.register(EchoTool())
    llm = ScriptedLLM(
        responses=[
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    content="I will call echo.",
                    tool_calls=(
                        ToolCallSpec(id="call-1", name="echo", arguments={"text": "hello"}),
                    ),
                ),
                finish_reason="tool_calls",
            ),
            LLMResponse(
                message=ChatMessage(role="assistant", content="done: hello"),
                finish_reason="stop",
            ),
        ]
    )
    loop = ReActLoopImpl(
        llm,
        registry,
        storage,
        clock=clock,
        id_gen=id_gen,
    )
    return loop, llm


@pytest.mark.asyncio
async def test_react_loop_one_round(storage, session, fixed_clock, fixed_uuid7):
    loop, llm = _build_loop(storage, clock=fixed_clock, id_gen=fixed_uuid7)
    agent = AgentDefinition(
        id="agent-x",
        name="echo-agent",
        system_prompt="You are an echo agent.",
        tools=["echo"],
        skills=[],
        model="glm-4",
        max_iters=4,
    )

    events_iter = await loop.run(agent=agent, user_input="please echo hello", session=session)
    events = [ev async for ev in events_iter]

    types = [ev.type for ev in events]
    assert types == ["thinking", "tool_call", "tool_result", "content", "done"]

    assert events[0].payload == {"text": "I will call echo."}
    assert events[1].payload == {
        "tool_call_id": "call-1",
        "name": "echo",
        "arguments": {"text": "hello"},
    }
    assert events[2].payload == {
        "tool_call_id": "call-1",
        "ok": True,
        "content": "hello",
    }
    assert events[3].payload == {"text": "done: hello", "delta": False}
    assert events[4].payload == {"finish_reason": "stop"}

    # Each event is also emitted to session.emit (storage path).
    assert [ev.id for ev in session.events] == [ev.id for ev in events]


@pytest.mark.asyncio
async def test_react_loop_rejects_unwhitelisted_tool(storage, session, fixed_clock, fixed_uuid7):
    """§10 — a non-whitelisted call MUST surface as ``tool_result{ok:false}``
    so the LLM can self-correct, not crash the loop."""
    registry = InMemoryToolRegistry()
    registry.register(EchoTool())
    llm = ScriptedLLM(
        responses=[
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    content="",
                    tool_calls=(
                        ToolCallSpec(id="c1", name="forbidden_tool", arguments={}),
                    ),
                ),
                finish_reason="tool_calls",
            ),
            LLMResponse(
                message=ChatMessage(role="assistant", content="giving up"),
                finish_reason="stop",
            ),
        ]
    )
    loop = ReActLoopImpl(llm, registry, storage, clock=fixed_clock, id_gen=fixed_uuid7)
    agent = AgentDefinition(
        id="a",
        name="a",
        system_prompt="",
        tools=["echo"],  # forbidden_tool NOT in whitelist
        skills=[],
        model="glm-4",
        max_iters=4,
    )
    events = [ev async for ev in await loop.run(agent=agent, user_input="x", session=session)]

    types = [ev.type for ev in events]
    assert types == ["tool_call", "tool_result", "content", "done"]
    assert events[1].payload["ok"] is False
    assert "not whitelisted" in events[1].payload["content"]


@pytest.mark.asyncio
async def test_react_loop_hits_max_iters(storage, session, fixed_clock, fixed_uuid7):
    """A loop that keeps calling tools must terminate at ``max_iters``."""
    registry = InMemoryToolRegistry()
    registry.register(EchoTool())
    responses = [
        LLMResponse(
            message=ChatMessage(
                role="assistant",
                content="",
                tool_calls=(ToolCallSpec(id=f"c{i}", name="echo", arguments={"text": "x"}),),
            ),
            finish_reason="tool_calls",
        )
        for i in range(6)
    ]
    llm = ScriptedLLM(responses=responses)
    loop = ReActLoopImpl(llm, registry, storage, clock=fixed_clock, id_gen=fixed_uuid7)
    agent = AgentDefinition(
        id="a",
        name="a",
        system_prompt="",
        tools=["echo"],
        skills=[],
        model="glm-4",
        max_iters=3,
    )
    events = [ev async for ev in await loop.run(agent=agent, user_input="x", session=session)]
    assert events[-1].type == "done"
    assert events[-1].payload["finish_reason"] == "max_iters"
