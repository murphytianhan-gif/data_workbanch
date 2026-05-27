"""Trivial Tool used across DoD tests — echoes its `text` argument back."""
from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.agent_engine.tool import Tool, ToolContext, ToolResult


class EchoTool(Tool):
    name = "echo"
    description = "Return the value of the ``text`` argument verbatim."
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }

    async def invoke(self, arguments: Mapping[str, Any], ctx: ToolContext) -> ToolResult:
        return ToolResult(ok=True, content=str(arguments.get("text", "")))
