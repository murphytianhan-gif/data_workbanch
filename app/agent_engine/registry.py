"""Per-agent in-memory tool registry — see §4.1."""
from __future__ import annotations

from collections.abc import Sequence

from app.agent_engine.llm import ToolDescriptor
from app.agent_engine.tool import Tool, ToolNotFound, ToolRegistry


class InMemoryToolRegistry(ToolRegistry):
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if not tool.name or not tool.name.isascii() or len(tool.name) > 64:
            raise ValueError(f"invalid tool name: {tool.name!r}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as e:
            raise ToolNotFound(name) from e

    def all(self) -> Sequence[Tool]:
        return list(self._tools.values())

    def descriptors(self) -> Sequence[ToolDescriptor]:
        return [
            ToolDescriptor(
                name=t.name,
                description=t.description,
                parameters=t.parameters_schema,
            )
            for t in self._tools.values()
        ]
