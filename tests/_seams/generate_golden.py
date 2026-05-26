"""Regenerate ``tests/fixtures/scenario_basic.golden.jsonl``.

Run as a module:

    python -m tests._seams.generate_golden

Re-run after any wire-format change so the golden + engine stay in sync.
A wire-format change always requires a ``[contract-change]`` review
beforehand — see §8.2.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.agent_engine.engine import ReActLoopImpl
from app.agent_engine.llm import ChatMessage, LLMResponse, ToolCallSpec
from app.agent_engine.react import AgentDefinition, SessionEvent
from app.agent_engine.registry import InMemoryToolRegistry
from app.agent_engine.serialization import encode_event, to_jsonl_line
from tests._seams.echo_tool import EchoTool
from tests._seams.inmemory_storage import InMemoryStorage
from tests._seams.mock_llm import ScriptedLLM

GOLDEN_PATH = Path(__file__).parent.parent / "fixtures" / "scenario_basic.golden.jsonl"


class _FakeSession:
    def __init__(self) -> None:
        self.session_id = "sess-1"
        self.agent_id = "agent-1"
        self.user_id = "user-1"
        self.turn = 0
        self.events: list[SessionEvent] = []

    async def emit(self, event: SessionEvent) -> None:
        self.events.append(event)


async def _run() -> bytes:
    storage = InMemoryStorage()
    session = _FakeSession()
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
    counter = {"n": 0}

    def fake_uuid7() -> str:
        counter["n"] += 1
        return f"00000000-0000-7000-8000-{counter['n']:012d}"

    loop = ReActLoopImpl(
        llm,
        registry,
        storage,
        clock=lambda: "2026-05-25T00:00:00.000Z",
        id_gen=fake_uuid7,
    )
    agent = AgentDefinition(
        id="agent-x",
        name="echo-agent",
        system_prompt="You are an echo agent.",
        tools=["echo"],
        skills=[],
        model="glm-4",
        max_iters=4,
    )
    events_iter = await loop.run(
        agent=agent, user_input="please echo hello", session=session
    )
    events = [ev async for ev in events_iter]
    encoded = [encode_event(ev) for ev in events]
    return "".join(to_jsonl_line(line) for line in encoded).encode("utf-8")


def main() -> None:
    data = asyncio.run(_run())
    GOLDEN_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN_PATH.write_bytes(data)
    print(f"wrote {GOLDEN_PATH} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
