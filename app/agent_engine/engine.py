"""Concrete ReAct loop — see docs/contracts/agent-interface.md §6.

``ReActLoopImpl`` drives one LLM × one ToolRegistry × (zero or more)
Skills. It emits ``SessionEvent``s on the byte-consistent §8 stream and
persists each one to storage through ``session.emit``.

Design notes
------------
* The loop yields events on the AsyncIterator returned by ``run``. Every
  emitted event also lands on ``session.emit`` so storage / SSE adapter
  see the same byte stream.
* Tool / Skill events can interleave with loop-produced events. A small
  ``asyncio.Queue`` fans those in so the outer iterator order matches
  emission order — mandatory for the §8.2 golden-file byte check.
* Whitelist (§7) is enforced before dispatch. A non-whitelisted name
  yields a ``tool_result{ok:false}`` instead of raising — §10 says the
  LLM must be able to self-correct.
* ``thinking`` is always emitted (§12.3); the SSE adapter filters when
  ``agent.expose_thinking`` is false. See ``sse_filter.py``.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from typing import Any

from app.agent_engine.clock import Clock, utc_now_rfc3339
from app.agent_engine.context import ConcreteToolContext
from app.agent_engine.errors import EngineError
from app.agent_engine.llm import ChatMessage, LLMClient, ToolCallSpec, ToolDescriptor
from app.agent_engine.react import (
    AgentDefinition,
    SessionEvent,
    SessionHandle,
)
from app.agent_engine.skill import Skill
from app.agent_engine.tool import Tool, ToolError, ToolRegistry, ToolResult
from app.agent_engine.uuid7 import uuid7
from app.storage.protocol import StorageProtocol

_QUEUE_SENTINEL: Any = object()


class ReActLoopImpl:
    """Production ReAct loop. Matches the ``ReActLoop`` Protocol structurally."""

    def __init__(
        self,
        llm: LLMClient,
        registry: ToolRegistry,
        storage: StorageProtocol,
        *,
        skills: Mapping[str, Skill] | None = None,
        clock: Clock = utc_now_rfc3339,
        id_gen: Any = uuid7,
    ) -> None:
        self._llm = llm
        self._registry = registry
        self._storage = storage
        self._skills: dict[str, Skill] = dict(skills or {})
        self._clock = clock
        self._id_gen = id_gen

    async def run(
        self,
        *,
        agent: AgentDefinition,
        user_input: str,
        session: SessionHandle,
    ) -> AsyncIterator[SessionEvent]:
        """Return the per-call event iterator.

        Coroutine return — caller awaits once, then async-iterates.
        """
        return self._iter_events(agent=agent, user_input=user_input, session=session)

    # ---------------------------------------------------------------- helpers

    def _make_event(
        self,
        *,
        session: SessionHandle,
        turn: int,
        iter_: int,
        type_: str,
        payload: dict[str, Any],
        parent_tool_call_id: str | None = None,
    ) -> SessionEvent:
        return SessionEvent(
            id=self._id_gen(),
            session_id=session.session_id,
            agent_id=session.agent_id,
            turn=turn,
            iter=iter_,
            type=type_,  # type: ignore[arg-type]
            payload=payload,
            parent_tool_call_id=parent_tool_call_id,
            created_at=self._clock(),
        )

    def _collect_descriptors(self, agent: AgentDefinition) -> list[ToolDescriptor]:
        whitelist = set(agent.tools) | set(agent.skills)
        descriptors: list[ToolDescriptor] = []
        for tool in self._registry.all():
            if tool.name in whitelist:
                descriptors.append(
                    ToolDescriptor(
                        name=tool.name,
                        description=tool.description,
                        parameters=tool.parameters_schema,
                    )
                )
        for skill_name in agent.skills:
            if skill_name in self._skills:
                sk = self._skills[skill_name]
                descriptors.append(
                    ToolDescriptor(
                        name=sk.name,
                        description=sk.description,
                        parameters=sk.parameters_schema,
                    )
                )
        return descriptors

    # ---------------------------------------------------------------- core

    async def _iter_events(
        self,
        *,
        agent: AgentDefinition,
        user_input: str,
        session: SessionHandle,
    ) -> AsyncIterator[SessionEvent]:
        queue: asyncio.Queue[Any] = asyncio.Queue()

        async def emit(event: SessionEvent) -> None:
            # Persist to storage first so the SSE consumer never observes
            # an event that is not yet on disk — matches §8 ordering.
            await session.emit(event)
            await queue.put(event)

        driver = asyncio.create_task(
            self._drive(agent=agent, user_input=user_input, session=session, emit=emit)
        )

        async def signal_done() -> None:
            try:
                await driver
            finally:
                await queue.put(_QUEUE_SENTINEL)

        signaller = asyncio.create_task(signal_done())

        try:
            while True:
                item = await queue.get()
                if item is _QUEUE_SENTINEL:
                    break
                yield item
        finally:
            if not driver.done():
                driver.cancel()
            await asyncio.gather(driver, signaller, return_exceptions=True)

    async def _drive(
        self,
        *,
        agent: AgentDefinition,
        user_input: str,
        session: SessionHandle,
        emit: Any,
    ) -> None:
        whitelist = set(agent.tools) | set(agent.skills)
        descriptors = self._collect_descriptors(agent)
        messages: list[ChatMessage] = [
            ChatMessage(role="system", content=agent.system_prompt),
            ChatMessage(role="user", content=user_input),
        ]

        turn = session.turn
        iter_ = 0
        finish_reason: str = "stop"

        try:
            for iter_ in range(agent.max_iters):
                resp = await self._llm.chat(
                    messages,
                    descriptors,
                    model=agent.model,
                    temperature=agent.temperature,
                    max_tokens=agent.max_tokens,
                    extra=agent.extra,
                )
                assistant = resp.message
                tool_calls = assistant.tool_calls or ()

                # Treat content with tool_calls as "thinking", per §6 ReAct.
                if assistant.content and tool_calls:
                    await emit(
                        self._make_event(
                            session=session,
                            turn=turn,
                            iter_=iter_,
                            type_="thinking",
                            payload={"text": assistant.content},
                        )
                    )

                if resp.finish_reason == "tool_calls" and tool_calls:
                    messages.append(assistant)
                    await self._dispatch_tools(
                        tool_calls=list(tool_calls),
                        agent=agent,
                        whitelist=whitelist,
                        session=session,
                        turn=turn,
                        iter_=iter_,
                        emit=emit,
                        messages=messages,
                    )
                    continue

                if resp.finish_reason == "stop":
                    if assistant.content:
                        await emit(
                            self._make_event(
                                session=session,
                                turn=turn,
                                iter_=iter_,
                                type_="content",
                                payload={"text": assistant.content, "delta": False},
                            )
                        )
                    finish_reason = "stop"
                    break

                if resp.finish_reason in ("length", "error"):
                    await emit(
                        self._make_event(
                            session=session,
                            turn=turn,
                            iter_=iter_,
                            type_="error",
                            payload={
                                "code": resp.finish_reason,
                                "message": f"LLM finished with {resp.finish_reason}",
                            },
                        )
                    )
                    finish_reason = "error"
                    break
            else:
                finish_reason = "max_iters"

        except EngineError as exc:  # pragma: no cover — covered in unit test
            await emit(
                self._make_event(
                    session=session,
                    turn=turn,
                    iter_=iter_,
                    type_="error",
                    payload={"code": type(exc).__name__, "message": str(exc)},
                )
            )
            finish_reason = "error"

        await emit(
            self._make_event(
                session=session,
                turn=turn,
                iter_=iter_,
                type_="done",
                payload={"finish_reason": finish_reason},
            )
        )

    async def _dispatch_tools(
        self,
        *,
        tool_calls: list[ToolCallSpec],
        agent: AgentDefinition,
        whitelist: set[str],
        session: SessionHandle,
        turn: int,
        iter_: int,
        emit: Any,
        messages: list[ChatMessage],
    ) -> None:
        # Emit all tool_call events first (one per call, parallel-safe).
        for call in tool_calls:
            await emit(
                self._make_event(
                    session=session,
                    turn=turn,
                    iter_=iter_,
                    type_="tool_call",
                    payload={
                        "tool_call_id": call.id,
                        "name": call.name,
                        "arguments": call.arguments,
                    },
                )
            )

        async def run_one(call: ToolCallSpec) -> tuple[ToolCallSpec, ToolResult]:
            if call.name not in whitelist:
                return call, ToolResult(
                    ok=False,
                    content=f"tool '{call.name}' not whitelisted for this agent",
                    error=ToolError(
                        code="not_whitelisted",
                        message=f"agent '{agent.id}' cannot call '{call.name}'",
                    ),
                )

            invoker: Tool
            if call.name in self._skills:
                # Skill super-tool: build a per-call adapter so the
                # inner mini-ReAct knows the outer call id.
                skill = self._skills[call.name]
                invoker = await skill.as_tool()
            else:
                try:
                    invoker = self._registry.get(call.name)
                except KeyError:
                    return call, ToolResult(
                        ok=False,
                        content=f"tool '{call.name}' not registered",
                        error=ToolError(code="not_registered", message=call.name),
                    )

            # Scope ctx.emit so skill internals get parent_tool_call_id.
            async def scoped_emit(ev: SessionEvent) -> None:
                tagged = (
                    ev
                    if ev.parent_tool_call_id is not None
                    else replace(ev, parent_tool_call_id=call.id)
                )
                await emit(tagged)

            ctx = ConcreteToolContext(
                session_id=session.session_id,
                agent_id=session.agent_id,
                user_id=session.user_id,
                storage=self._storage,
                emit=scoped_emit,
            )
            try:
                result = await invoker.invoke(call.arguments, ctx)
            except Exception as exc:  # tools should not raise; defend anyway
                result = ToolResult(
                    ok=False,
                    content=f"tool '{call.name}' raised: {exc}",
                    error=ToolError(code="exception", message=str(exc)),
                )
            return call, result

        results = await asyncio.gather(*(run_one(c) for c in tool_calls))

        for call, result in results:
            payload: dict[str, Any] = {
                "tool_call_id": call.id,
                "ok": result.ok,
                "content": result.content,
            }
            if result.raw is not None:
                payload["raw"] = result.raw
            await emit(
                self._make_event(
                    session=session,
                    turn=turn,
                    iter_=iter_,
                    type_="tool_result",
                    payload=payload,
                )
            )
            messages.append(
                ChatMessage(role="tool", content=result.content, tool_call_id=call.id)
            )
