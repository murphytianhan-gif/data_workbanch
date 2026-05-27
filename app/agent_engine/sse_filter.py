"""SSE-side ``expose_thinking`` filter — see §12.3.

The ReAct loop always emits ``thinking`` events so they land in jsonl
storage. The routing layer's SSE adapter MUST elide them from the wire
stream when ``agent.expose_thinking`` is false. This helper centralises
the predicate so engine tests and the routing layer agree exactly.
"""
from __future__ import annotations

from app.agent_engine.react import AgentDefinition, SessionEvent


def event_visible_on_sse(event: SessionEvent, agent: AgentDefinition) -> bool:
    """Return True iff this event should reach the SSE consumer."""
    if event.type == "thinking" and not agent.expose_thinking:
        return False
    return True
