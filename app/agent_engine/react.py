"""ReAct loop + SessionEvent + AgentDefinition.

See docs/contracts/agent-interface.md §6, §7, §8.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


EventType = Literal[
    "thinking",
    "tool_call",
    "tool_result",
    "content",
    "done",
    "error",
]

FinishReason = Literal["stop", "max_iters", "error"]


@dataclass(frozen=True)
class AgentDefinition:
    """User-facing role binding.

    A whitelist of tools/skills, the model, the system prompt, and the
    §12.3 expose_thinking flag. Everything an outer caller needs to
    summon this agent.
    """

    id: str
    name: str
    system_prompt: str
    tools: Sequence[str]
    skills: Sequence[str]
    model: str
    temperature: float = 0.2
    max_tokens: int | None = None
    max_iters: int = 12
    expose_thinking: bool = True
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SessionEvent:
    """Single event on the byte-consistent SSE + jsonl stream.

    Wire format: one JSON object per event. SSE wraps with
    ``data: <json>\\n\\n``; jsonl wraps with ``<json>\\n``. The payload
    bytes are identical — see §8.2 golden-file rule.
    """

    id: str
    session_id: str
    agent_id: str
    turn: int
    iter: int
    type: EventType
    payload: dict[str, Any]
    parent_tool_call_id: str | None = None
    created_at: str = ""


class SessionHandle(Protocol):
    """Opaque handle the engine receives from the routing layer.

    Carries identity + the storage-backed emit channel. The engine never
    constructs this directly; routing layer instantiates and passes in.
    """

    session_id: str
    agent_id: str
    user_id: str
    turn: int

    async def emit(self, event: SessionEvent) -> None: ...


class ReActLoop(Protocol):
    async def run(
        self,
        *,
        agent: AgentDefinition,
        user_input: str,
        session: SessionHandle,
    ) -> AsyncIterator[SessionEvent]: ...
