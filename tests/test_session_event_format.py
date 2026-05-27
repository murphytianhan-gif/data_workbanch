"""DoD #4 — SessionEvent serialisation is byte-identical between SSE and jsonl.

We compare against a golden jsonl produced by the same scenario as
``test_react_loop`` so the wire format and on-disk format share the
same bytes (§8.2). The test stages both sides through one canonical
encoder; a divergence would surface as a byte-diff here.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.agent_engine.engine import ReActLoopImpl
from app.agent_engine.llm import ChatMessage, LLMResponse, ToolCallSpec
from app.agent_engine.react import AgentDefinition
from app.agent_engine.registry import InMemoryToolRegistry
from app.agent_engine.serialization import encode_event, to_jsonl_line, to_sse_frame
from tests._seams.echo_tool import EchoTool
from tests._seams.inmemory_storage import InMemoryStorage
from tests._seams.mock_llm import ScriptedLLM

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.asyncio
async def test_session_event_jsonl_matches_golden(
    storage: InMemoryStorage, session, fixed_clock, fixed_uuid7
):
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
    loop = ReActLoopImpl(llm, registry, storage, clock=fixed_clock, id_gen=fixed_uuid7)
    agent = AgentDefinition(
        id="agent-x",
        name="echo-agent",
        system_prompt="You are an echo agent.",
        tools=["echo"],
        skills=[],
        model="glm-4",
        max_iters=4,
    )

    events = [
        ev async for ev in await loop.run(
            agent=agent, user_input="please echo hello", session=session
        )
    ]

    # Encode once; that same string powers both sides of the wire.
    encoded = [encode_event(ev) for ev in events]
    jsonl_bytes = "".join(to_jsonl_line(line) for line in encoded).encode("utf-8")
    sse_bytes = "".join(to_sse_frame(line) for line in encoded).encode("utf-8")

    golden_path = FIXTURES / "scenario_basic.golden.jsonl"
    golden = golden_path.read_bytes()
    assert jsonl_bytes == golden, (
        f"jsonl bytes differ from {golden_path}\n"
        f"--- expected ---\n{golden.decode('utf-8')}\n"
        f"--- got ---\n{jsonl_bytes.decode('utf-8')}"
    )

    # SSE wraps jsonl lines with `data: ` + double newline.
    sse_lines = sse_bytes.decode("utf-8").split("\n\n")
    json_payloads_from_sse = [
        line[len("data: "):] for line in sse_lines if line.startswith("data: ")
    ]
    assert json_payloads_from_sse == encoded
