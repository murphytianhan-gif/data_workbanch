"""Scripted LLM seam for engine tests — §11.

Returns a fixed sequence of ``LLMResponse`` objects, one per call to
``chat``. Each test scripts the exact think/tool_call/content turns the
ReAct loop should drive on.
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass

from app.agent_engine.llm import (
    ChatMessage,
    LLMDelta,
    LLMResponse,
    ToolDescriptor,
)


@dataclass
class ScriptedLLM:
    """Hand the loop a list of LLMResponses; each chat() pops one."""

    responses: list[LLMResponse]

    def __post_init__(self) -> None:
        self.calls: list[dict[str, object]] = []
        self._idx = 0

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDescriptor] | None = None,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> LLMResponse:
        if self._idx >= len(self.responses):
            raise AssertionError(
                f"ScriptedLLM ran out of responses (calls={self._idx + 1})"
            )
        self.calls.append(
            {
                "messages": list(messages),
                "tools": list(tools or []),
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "extra": dict(extra or {}),
            }
        )
        resp = self.responses[self._idx]
        self._idx += 1
        return resp

    def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDescriptor] | None = None,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        extra: Mapping[str, object] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        # Engine MVP does not exercise stream(); minimal stub for protocol fit.
        async def _gen() -> AsyncIterator[LLMDelta]:
            if False:  # pragma: no cover
                yield  # type: ignore[unreachable]

        return _gen()
