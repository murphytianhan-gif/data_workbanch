"""Concrete ToolContext.

A ToolContext is built per tool call. The ``emit`` channel is a queue
push owned by the surrounding ReAct loop; tools never see the queue
directly.

Not a ``@dataclass`` on purpose — a Protocol that names ``emit`` as a
``Callable`` attribute mypy-checks against a *settable* attribute, and
dataclass field annotations of ``Callable`` confuse the variance check
in mypy strict. A plain ``__init__`` keeps the structural match clean.
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable

from app.agent_engine.react import SessionEvent
from app.storage.protocol import StorageProtocol


class ConcreteToolContext:
    session_id: str
    agent_id: str
    user_id: str
    storage: StorageProtocol
    emit: Callable[[SessionEvent], Awaitable[None]]

    def __init__(
        self,
        *,
        session_id: str,
        agent_id: str,
        user_id: str,
        storage: StorageProtocol,
        emit: Callable[[SessionEvent], Awaitable[None]],
    ) -> None:
        self.session_id = session_id
        self.agent_id = agent_id
        self.user_id = user_id
        self.storage = storage
        self.emit = emit
