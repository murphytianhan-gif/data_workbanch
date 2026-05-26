"""DoD #3 — A Skill exposes a 2-step mini-ReAct as one outer Tool result.

Outer LLM sees exactly one ``tool_call`` + one ``tool_result`` for the
skill. Inner events (the two-step mini-ReAct) are tagged with
``parent_tool_call_id`` and reach the SessionEvent stream.
"""
from __future__ import annotations

import pytest

from app.agent_engine.engine import ReActLoopImpl
from app.agent_engine.llm import ChatMessage, LLMResponse, ToolCallSpec
from app.agent_engine.react import AgentDefinition
from app.agent_engine.registry import InMemoryToolRegistry
from app.agent_engine.skill_impl import SkillImpl
from tests._seams.echo_tool import EchoTool
from tests._seams.inmemory_storage import InMemoryStorage
from tests._seams.mock_llm import ScriptedLLM


@pytest.mark.asyncio
async def test_skill_appears_to_outer_as_single_tool(
    storage: InMemoryStorage, session, fixed_clock, fixed_uuid7
):
    # The mini-ReAct (inside the skill) goes:
    #   1. echo "a" -> tool_result "a"
    #   2. final content "ab"
    inner_llm = ScriptedLLM(
        responses=[
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    content="step 1: echo a",
                    tool_calls=(
                        ToolCallSpec(id="inner-1", name="echo", arguments={"text": "a"}),
                    ),
                ),
                finish_reason="tool_calls",
            ),
            LLMResponse(
                message=ChatMessage(role="assistant", content="ab"),
                finish_reason="stop",
            ),
        ]
    )
    inner_registry = InMemoryToolRegistry()
    inner_registry.register(EchoTool())
    inner_loop = ReActLoopImpl(
        inner_llm, inner_registry, storage, clock=fixed_clock, id_gen=fixed_uuid7
    )

    skill = SkillImpl(
        name="say_ab",
        description="Echo 'a' then return 'ab'.",
        parameters_schema={"type": "object", "properties": {}},
        inner_tools=["echo"],
        inner_prompt="You are a 2-step skill.",
        inner_loop=inner_loop,
        inner_max_iters=4,
    )

    # The outer LLM only calls the skill once.
    outer_llm = ScriptedLLM(
        responses=[
            LLMResponse(
                message=ChatMessage(
                    role="assistant",
                    content="I'll run the say_ab skill.",
                    tool_calls=(
                        ToolCallSpec(id="outer-1", name="say_ab", arguments={}),
                    ),
                ),
                finish_reason="tool_calls",
            ),
            LLMResponse(
                message=ChatMessage(role="assistant", content="final: ab"),
                finish_reason="stop",
            ),
        ]
    )
    outer_registry = InMemoryToolRegistry()  # no atomic tools at outer level
    outer_loop = ReActLoopImpl(
        outer_llm,
        outer_registry,
        storage,
        skills={"say_ab": skill},
        clock=fixed_clock,
        id_gen=fixed_uuid7,
    )

    agent = AgentDefinition(
        id="outer-agent",
        name="outer-agent",
        system_prompt="",
        tools=[],
        skills=["say_ab"],
        model="glm-4",
        max_iters=4,
    )
    events = [
        ev async for ev in await outer_loop.run(
            agent=agent, user_input="run the skill", session=session
        )
    ]

    outer_events = [ev for ev in events if ev.parent_tool_call_id is None]
    inner_events = [ev for ev in events if ev.parent_tool_call_id is not None]

    # Outer LLM sees exactly one tool_call + one tool_result pair.
    outer_types = [ev.type for ev in outer_events]
    assert outer_types == ["thinking", "tool_call", "tool_result", "content", "done"]
    outer_tool_result = next(ev for ev in outer_events if ev.type == "tool_result")
    assert outer_tool_result.payload["content"] == "ab"
    assert outer_tool_result.payload["ok"] is True

    # Inner events all carry the outer call id.
    assert inner_events, "expected inner skill events with parent_tool_call_id"
    assert all(ev.parent_tool_call_id == "outer-1" for ev in inner_events)
    inner_types = [ev.type for ev in inner_events]
    assert inner_types[:3] == ["thinking", "tool_call", "tool_result"]
    assert "content" in inner_types and "done" in inner_types
