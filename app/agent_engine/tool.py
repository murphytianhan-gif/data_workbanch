"""Tool protocol + registry — see docs/contracts/agent-interface.md §4.

A Tool is an atomic, LLM-callable unit. One call, one result. No internal loop.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol

from app.agent_engine.llm import ToolDescriptor

if TYPE_CHECKING:
    from app.agent_engine.react import SessionEvent
    from app.storage.protocol import StorageProtocol


@dataclass(frozen=True)
class ToolError:
    code: str
    message: str
    retriable: bool = False


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    content: str
    raw: Any | None = None
    error: ToolError | None = None


class ToolContext(Protocol):
    """Per-invocation context. Never module-global, never imported across tools."""

    session_id: str
    agent_id: str
    user_id: str
    storage: StorageProtocol
    emit: Callable[[SessionEvent], Awaitable[None]]


class Tool(Protocol):
    name: str
    description: str
    parameters_schema: dict[str, Any]

    async def invoke(
        self,
        arguments: Mapping[str, Any],
        ctx: ToolContext,
    ) -> ToolResult: ...


class ToolNotFound(KeyError):
    """Raised when a name lookup against ToolRegistry fails."""


class ToolRegistry(Protocol):
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...
    def all(self) -> Sequence[Tool]: ...
    def descriptors(self) -> Sequence[ToolDescriptor]: ...
