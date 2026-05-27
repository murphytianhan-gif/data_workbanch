"""Shared fixtures for storage tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.storage.markdown_store import MarkdownStore

USER_ID = "01940000-0000-7000-8000-000000000001"
PROJECT_ID = "01940000-0000-7000-8000-000000000002"


@pytest.fixture
def store(tmp_path: Path) -> MarkdownStore:
    return MarkdownStore(tmp_path)


@pytest.fixture
def user_id() -> str:
    return USER_ID


@pytest.fixture
def project_id() -> str:
    return PROJECT_ID
"""Shared fixtures for engine tests."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass

import pytest

from app.agent_engine.react import SessionEvent
from tests._seams.inmemory_storage import InMemoryStorage


@dataclass
class FakeSessionHandle:
    """Trivial SessionHandle that buffers emitted events for assertions."""

    session_id: str
    agent_id: str
    user_id: str
    turn: int
    events: list[SessionEvent]

    async def emit(self, event: SessionEvent) -> None:
        self.events.append(event)


def make_session(
    *,
    session_id: str = "sess-1",
    agent_id: str = "agent-1",
    user_id: str = "user-1",
    turn: int = 0,
) -> FakeSessionHandle:
    return FakeSessionHandle(
        session_id=session_id,
        agent_id=agent_id,
        user_id=user_id,
        turn=turn,
        events=[],
    )


@pytest.fixture
def storage() -> InMemoryStorage:
    return InMemoryStorage()


@pytest.fixture
def session() -> FakeSessionHandle:
    return make_session()


@pytest.fixture
def fixed_clock() -> Callable[[], str]:
    """Pinned RFC3339 timestamp for golden-file determinism."""
    return lambda: "2026-05-25T00:00:00.000Z"


@pytest.fixture
def fixed_uuid7() -> Iterator[Callable[[], str]]:
    """Monotonic counter-based UUIDv7-shaped string for byte-equal output."""
    counter = {"n": 0}

    def gen() -> str:
        counter["n"] += 1
        return f"00000000-0000-7000-8000-{counter['n']:012d}"

    yield gen
