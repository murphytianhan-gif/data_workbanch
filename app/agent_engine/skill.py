"""Skill protocol — see docs/contracts/agent-interface.md §5.

A Skill runs a mini-ReAct internally and exposes itself to the outer
loop as a single Tool (the §12.1 'super-tool' shape).
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

from app.agent_engine.tool import Tool


class Skill(Protocol):
    name: str
    description: str
    parameters_schema: dict[str, Any]

    inner_tools: Sequence[str]
    inner_prompt: str
    inner_model: str | None
    inner_max_iters: int

    async def as_tool(self) -> Tool:
        """Return the adapter object the outer ReAct loop sees.

        The outer loop only ever observes a single tool_call + tool_result
        pair per Skill invocation. Internal events (thinking / nested
        tool_call / nested tool_result) are emitted with
        ``parent_tool_call_id`` set to the outer call's id.
        """
        ...
