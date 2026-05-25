"""Storage protocol — the only seam between engine and storage.

See docs/contracts/data-model.md for the markdown layout + frontmatter
schemas; this file is just the Python protocol the engine talks to.

OCC (optimistic concurrency control): every write takes an
``if_updated_at`` argument; storage rejects with ``StaleWriteError`` if
the on-disk ``updated_at`` doesn't match. precipitate() is atomic
across three files (new semantic entry / deliverable backref / observation).
"""
from __future__ import annotations

from collections.abc import AsyncIterator, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, Protocol


ResourceKind = Literal[
    "project",
    "framework",
    "deliverable",
    "reference",
    "sql",
    "observation",
    "agent",
    "session",
    "semantic_entry",
]


@dataclass(frozen=True)
class ResourceRef:
    kind: ResourceKind
    id: str                  # stable UUID; not the filename
    user_id: str
    project_id: str | None   # None for user-scoped resources (e.g. agents)


@dataclass(frozen=True)
class Resource:
    ref: ResourceRef
    frontmatter: Mapping[str, Any]
    body: str
    updated_at: datetime     # for OCC


class StaleWriteError(Exception):
    """Raised by write/update when ``if_updated_at`` doesn't match disk."""


class ResourceNotFound(KeyError):
    pass


class PrecipitateViolation(ValueError):
    """Raised by precipitate() if any of the three inputs is malformed.

    Notably: §12.5 rejection — a raw rowset in deliverable frontmatter.
    """


@dataclass(frozen=True)
class PrecipitateInputs:
    """Inputs to the atomic 沉淀 飞轮 operation.

    1. New ``semantic_entry`` resource.
    2. Backref written into the source ``deliverable``'s frontmatter.
    3. Observation row appended (timestamp, agent_id, source kind/id).
    """

    semantic_entry_frontmatter: Mapping[str, Any]
    semantic_entry_body: str
    source_deliverable_ref: ResourceRef
    source_deliverable_expected_updated_at: datetime  # OCC
    observation_payload: Mapping[str, Any]


class StorageProtocol(Protocol):
    """Single-process, file-system-backed storage.

    Engine imports this protocol only. Concrete implementation lives in
    `app.storage.markdown_store` (波次 1A / MUR-8).
    """

    # ---- single-resource CRUD ----
    async def get(self, ref: ResourceRef) -> Resource: ...

    async def list(
        self,
        *,
        kind: ResourceKind,
        user_id: str,
        project_id: str | None = None,
    ) -> Sequence[ResourceRef]: ...

    async def create(
        self,
        ref: ResourceRef,
        frontmatter: Mapping[str, Any],
        body: str,
    ) -> Resource: ...

    async def update(
        self,
        ref: ResourceRef,
        frontmatter: Mapping[str, Any],
        body: str,
        *,
        if_updated_at: datetime,    # OCC; raises StaleWriteError on mismatch
    ) -> Resource: ...

    async def delete(
        self,
        ref: ResourceRef,
        *,
        if_updated_at: datetime,
    ) -> None: ...                  # tombstone, not physical removal

    # ---- session event jsonl ----
    async def append_session_event(self, session_id: str, event_json: str) -> None:
        """Append one already-serialised SessionEvent line to the session jsonl.

        The bytes MUST match what the SSE adapter emits (§8 byte-consistency).
        """
        ...

    async def read_session_events(
        self,
        session_id: str,
    ) -> AsyncIterator[str]: ...    # yields raw jsonl lines, no parsing

    # ---- atomic precipitate (沉淀飞轮) ----
    async def precipitate(self, inputs: PrecipitateInputs) -> Resource:
        """Atomic three-file operation; engine cannot decompose into steps.

        Raises PrecipitateViolation on §12.5 violations (raw rowset in
        deliverable frontmatter).
        """
        ...
