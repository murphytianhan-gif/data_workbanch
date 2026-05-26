"""DoD #5 — ``expose_thinking=False`` elides ``thinking`` from SSE, keeps it in jsonl.

The ReAct loop always emits thinking events (§12.3). The SSE adapter
filters them; the jsonl on-disk stream does not. This test pins both
halves.
"""
from __future__ import annotations

import pytest

from app.agent_engine.engine import ReActLoopImpl
from app.agent_engine.llm import ChatMessage, LLMResponse, ToolCallSpec
from app.agent_engine.react import AgentDefinition
from app.agent_engine.registry import InMemoryToolRegistry
from app.agent_engine.sse_filter import event_visible_on_sse
from tests._seams.echo_tool import EchoTool
from tests._seams.inmemory_storage import InMemoryStorage
from tests._seams.mock_llm import ScriptedLLM


@pytest.mark.asyncio
async def test_thinking_persisted_but_not_sse_when_disabled(
    storage: InMemoryStorage, session, fixed_clock, fixed_uuid7
):
    registry = InMemoryToolRegistry()
    registry.register(EchoTool())
    llm = ScriptedLLM(
        responses=[
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    content="I am thinking...",
                    tool_calls=(
                        ToolCallSpec(id="c1", name="echo", arguments={"text": "x"}),
                    ),
                ),
                finish_reason="tool_calls",
            ),
            LLMResponse(
                message=ChatMessage(role="assistant", content="final"),
                finish_reason="stop",
            ),
        ]
    )
    loop = ReActLoopImpl(llm, registry, storage, clock=fixed_clock, id_gen=fixed_uuid7)
    agent = AgentDefinition(
        id="a",
        name="a",
        system_prompt="",
        tools=["echo"],
        skills=[],
        model="glm-4",
        max_iters=4,
        expose_thinking=False,
    )

    events = [ev async for ev in await loop.run(agent=agent, user_input="x", session=session)]

    # jsonl side — storage gets every event, including thinking.
    persisted_types = [ev.type for ev in session.events]
    assert "thinking" in persisted_types

    # SSE side — filter elides thinking.
    sse_types = [ev.type for ev in events if event_visible_on_sse(ev, agent)]
    assert "thinking" not in sse_types
    # Non-thinking events still flow.
    assert sse_types == ["tool_call", "tool_result", "content", "done"]


@pytest.mark.asyncio
async def test_thinking_in_sse_when_enabled(
    storage: InMemoryStorage, session, fixed_clock, fixed_uuid7
):
    registry = InMemoryToolRegistry()
    registry.register(EchoTool())
    llm = ScriptedLLM(
        responses=[
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    content="thinking out loud",
                    tool_calls=(
                        ToolCallSpec(id="c1", name="echo", arguments={"text": "x"}),
                    ),
                ),
                finish_reason="tool_calls",
            ),
            LLMResponse(
                message=ChatMessage(role="assistant", content="done"),
                finish_reason="stop",
            ),
        ]
    )
    loop = ReActLoopImpl(llm, registry, storage, clock=fixed_clock, id_gen=fixed_uuid7)
    agent = AgentDefinition(
        id="a",
        name="a",
        system_prompt="",
        tools=["echo"],
        skills=[],
        model="glm-4",
        max_iters=4,
        expose_thinking=True,
    )
    events = [ev async for ev in await loop.run(agent=agent, user_input="x", session=session)]
    sse_types = [ev.type for ev in events if event_visible_on_sse(ev, agent)]
    assert sse_types[0] == "thinking"
