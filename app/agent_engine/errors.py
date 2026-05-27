"""Engine-internal exceptions.

ToolError lives in ``tool.py`` because it is part of the engine surface.
Everything here is raised by engine code paths the LLM does not see.
"""
from __future__ import annotations


class EngineError(Exception):
    """Base for engine-internal failures."""


class WhitelistViolation(EngineError):
    """A tool_call referenced a name not in agent.tools ∪ agent.skills."""


class MaxItersExceeded(EngineError):
    """ReAct loop ran past ``agent.max_iters`` without a stop."""


class LLMTransportError(EngineError):
    """GLM gateway HTTP transport failure after retries exhausted."""


class MCPTransportError(EngineError):
    """MCP JSON-RPC transport failure after retries exhausted."""


class RawRowsetPersistError(ValueError):
    """§12.5 — engine-side guard that refuses to surface raw row sets.

    The storage layer raises ``PrecipitateViolation`` for the equivalent
    on the write side; this one fires at the dclaw tool boundary before
    the rowset can ever reach storage.
    """
