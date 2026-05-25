"""MCP transport skeleton — see docs/contracts/agent-interface.md §9.

Wave 1B (MUR-9) ships transport layer only. Server responses are mocked.
Real Cooper / dclaw integration moves to the end of Wave 1 or Wave 2.
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.agent_engine.llm import ToolDescriptor
from app.agent_engine.tool import ToolResult


class MCPClient(Protocol):
    server_id: str
    async def list_tools(self) -> Sequence[ToolDescriptor]: ...
    async def invoke(self, name: str, arguments: Mapping[str, Any]) -> ToolResult: ...
