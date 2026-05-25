"""LLM adapter protocol — see docs/contracts/agent-interface.md §3.

Provider-agnostic. GLM-specific knobs travel through `extra`.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, Protocol


Role = Literal["system", "user", "assistant", "tool"]


@dataclass(frozen=True)
class TokenUsage:
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True)
class ToolCallSpec:
    id: str
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class ChatMessage:
    role: Role
    content: str
    tool_call_id: str | None = None
    tool_calls: tuple[ToolCallSpec, ...] | None = None


@dataclass(frozen=True)
class ToolDescriptor:
    name: str
    description: str
    parameters: dict[str, Any]


FinishReason = Literal["stop", "tool_calls", "length", "error"]


@dataclass(frozen=True)
class LLMResponse:
    message: ChatMessage
    finish_reason: FinishReason
    usage: TokenUsage | None = None


@dataclass(frozen=True)
class LLMDelta:
    """One chunk in a streaming response."""

    type: Literal["content", "tool_call", "finish"]
    content_delta: str | None = None
    tool_call: ToolCallSpec | None = None
    finish_reason: FinishReason | None = None


class LLMClient(Protocol):
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDescriptor] | None = None,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> LLMResponse: ...

    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDescriptor] | None = None,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[LLMDelta]: ...
