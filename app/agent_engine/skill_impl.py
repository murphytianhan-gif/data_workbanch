"""Concrete Skill — mini-ReAct adapted as a super-Tool (§5, §12.1).

A SkillImpl carries its own scoped tool whitelist + system prompt and
runs an inner ``ReActLoopImpl`` when invoked. To the outer ReAct loop,
``as_tool()`` returns a vanilla ``Tool`` — the outer LLM only ever sees
one ``tool_call`` + one ``tool_result`` per skill invocation.

Inner events flow through the outer ``ToolContext.emit`` so the outer
loop tags them with ``parent_tool_call_id`` (see ``engine._dispatch_tools``).
"""
from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.agent_engine.engine import ReActLoopImpl
from app.agent_engine.react import AgentDefinition, SessionEvent
from app.agent_engine.tool import Tool, ToolContext, ToolResult


@dataclass
class _InnerSession:
    """SessionHandle implementation for a skill's mini-ReAct.

    Routes ``emit`` straight through the outer ToolContext.emit, which
    the outer loop has already wrapped to inject ``parent_tool_call_id``.
    """

    session_id: str
    agent_id: str
    user_id: str
    turn: int
    emit_fn: Callable[[SessionEvent], Awaitable[None]] = field(repr=False)

    async def emit(self, event: SessionEvent) -> None:
        await self.emit_fn(event)


class SkillImpl:
    """Production Skill. Structurally matches the ``Skill`` Protocol."""

    name: str
    description: str
    parameters_schema: dict[str, Any]
    inner_tools: Sequence[str]
    inner_prompt: str
    inner_model: str | None
    inner_max_iters: int

    def __init__(
        self,
        *,
        name: str,
        description: str,
        parameters_schema: dict[str, Any],
        inner_tools: Sequence[str],
        inner_prompt: str,
        inner_loop: ReActLoopImpl,
        inner_model: str | None = None,
        inner_max_iters: int = 6,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters_schema = parameters_schema
        self.inner_tools = list(inner_tools)
        self.inner_prompt = inner_prompt
        self.inner_model = inner_model
        self.inner_max_iters = inner_max_iters
        self._inner_loop = inner_loop

    async def as_tool(self) -> Tool:
        return _SkillAdapter(self)


class _SkillAdapter:
    """The single Tool surface the outer ReAct LLM sees."""

    def __init__(self, skill: SkillImpl) -> None:
        self._skill = skill
        self.name = skill.name
        self.description = skill.description
        self.parameters_schema = skill.parameters_schema

    async def invoke(
        self,
        arguments: Mapping[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        inner_session = _InnerSession(
            session_id=ctx.session_id,
            agent_id=ctx.agent_id,
            user_id=ctx.user_id,
            turn=0,
            emit_fn=ctx.emit,
        )
        inner_agent = AgentDefinition(
            id=f"skill:{self._skill.name}",
            name=self._skill.name,
            system_prompt=self._skill.inner_prompt,
            tools=list(self._skill.inner_tools),
            skills=[],  # MVP: no nested skills (§5).
            model=self._skill.inner_model or "skill-default",
            max_iters=self._skill.inner_max_iters,
            expose_thinking=True,  # inner is observable; outer filter handles SSE.
        )
        user_input = json.dumps(arguments, ensure_ascii=False, sort_keys=True)

        last_content = ""
        finish_reason: str | None = None
        events_iter = await self._skill._inner_loop.run(
            agent=inner_agent,
            user_input=user_input,
            session=inner_session,  # structural SessionHandle
        )
        async for ev in events_iter:
            if ev.type == "content" and ev.payload.get("delta") is False:
                last_content = str(ev.payload.get("text", ""))
            if ev.type == "done":
                finish_reason = str(ev.payload.get("finish_reason", "stop"))

        ok = finish_reason == "stop"
        return ToolResult(
            ok=ok,
            content=last_content
            if last_content
            else f"skill '{self._skill.name}' produced no content",
        )
