"""In-memory storage seam for engine tests — §11.

A dict + ``asyncio.Lock`` impl of ``StorageProtocol``. Preserves OCC
semantics (``if_updated_at`` mismatch → ``StaleWriteError``) and the
§12.5 precipitate rejection without ever touching disk.

Lives under ``tests/`` so the engine's import-linter rule
(``app.agent_engine -> app.storage`` forbidden) is not affected.
"""
from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

from app.storage.protocol import (
    PrecipitateInputs,
    PrecipitateViolation,
    Resource,
    ResourceKind,
    ResourceNotFound,
    ResourceRef,
    StaleWriteError,
)


def _looks_like_rowset(value: Any, *, cap: int = 200) -> bool:
    return (
        isinstance(value, list)
        and len(value) > cap
        and all(isinstance(r, dict) for r in value[:1])
    )


class InMemoryStorage:
    """Thread-safe via a single asyncio.Lock; good enough for tests."""

    def __init__(self) -> None:
        self._by_ref: dict[tuple[ResourceKind, str], Resource] = {}
        self._session_events: dict[str, list[str]] = {}
        self._lock = asyncio.Lock()

    # ---------------- single-resource CRUD ----------------

    async def get(self, ref: ResourceRef) -> Resource:
        key = (ref.kind, ref.id)
        async with self._lock:
            if key not in self._by_ref:
                raise ResourceNotFound(f"{ref.kind}/{ref.id}")
            return self._by_ref[key]

    async def list(
        self,
        *,
        kind: ResourceKind,
        user_id: str,
        project_id: str | None = None,
    ) -> Sequence[ResourceRef]:
        async with self._lock:
            return [
                r.ref
                for (k, _), r in self._by_ref.items()
                if k == kind
                and r.ref.user_id == user_id
                and (project_id is None or r.ref.project_id == project_id)
            ]

    async def create(
        self,
        ref: ResourceRef,
        frontmatter: Mapping[str, Any],
        body: str,
    ) -> Resource:
        async with self._lock:
            key = (ref.kind, ref.id)
            if key in self._by_ref:
                raise ValueError(f"already exists: {ref.kind}/{ref.id}")
            resource = Resource(
                ref=ref,
                frontmatter=dict(frontmatter),
                body=body,
                updated_at=datetime.now(UTC),
            )
            self._by_ref[key] = resource
            return resource

    async def update(
        self,
        ref: ResourceRef,
        frontmatter: Mapping[str, Any],
        body: str,
        *,
        if_updated_at: datetime,
    ) -> Resource:
        async with self._lock:
            key = (ref.kind, ref.id)
            if key not in self._by_ref:
                raise ResourceNotFound(f"{ref.kind}/{ref.id}")
            current = self._by_ref[key]
            if current.updated_at != if_updated_at:
                raise StaleWriteError(
                    f"OCC mismatch on {ref.kind}/{ref.id}: "
                    f"on-disk={current.updated_at}, expected={if_updated_at}"
                )
            updated = Resource(
                ref=ref,
                frontmatter=dict(frontmatter),
                body=body,
                updated_at=datetime.now(UTC),
            )
            self._by_ref[key] = updated
            return updated

    async def delete(self, ref: ResourceRef, *, if_updated_at: datetime) -> None:
        async with self._lock:
            key = (ref.kind, ref.id)
            if key not in self._by_ref:
                raise ResourceNotFound(f"{ref.kind}/{ref.id}")
            current = self._by_ref[key]
            if current.updated_at != if_updated_at:
                raise StaleWriteError(
                    f"OCC mismatch on {ref.kind}/{ref.id}"
                )
            del self._by_ref[key]

    # ---------------- session events jsonl ----------------

    async def append_session_event(self, session_id: str, event_json: str) -> None:
        async with self._lock:
            self._session_events.setdefault(session_id, []).append(event_json)

    async def read_session_events(self, session_id: str) -> AsyncIterator[str]:
        async with self._lock:
            lines = list(self._session_events.get(session_id, []))

        async def _gen() -> AsyncIterator[str]:
            for line in lines:
                yield line

        return _gen()

    # ---------------- precipitate ----------------

    async def precipitate(self, inputs: PrecipitateInputs) -> Resource:
        for k, v in inputs.semantic_entry_frontmatter.items():
            if _looks_like_rowset(v):
                raise PrecipitateViolation(
                    f"§12.5 violation: raw rowset not persistable (key={k!r}, len={len(v)})"
                )

        async with self._lock:
            src_key = (
                inputs.source_deliverable_ref.kind,
                inputs.source_deliverable_ref.id,
            )
            if src_key not in self._by_ref:
                raise ResourceNotFound(
                    f"{inputs.source_deliverable_ref.kind}/{inputs.source_deliverable_ref.id}"
                )
            current = self._by_ref[src_key]
            if current.updated_at != inputs.source_deliverable_expected_updated_at:
                raise StaleWriteError("OCC mismatch on source deliverable")

            # 1. new semantic_entry
            new_ref = ResourceRef(
                kind="semantic_entry",
                id=str(inputs.semantic_entry_frontmatter.get("id", "")),
                user_id=inputs.source_deliverable_ref.user_id,
                project_id=inputs.source_deliverable_ref.project_id,
            )
            new_resource = Resource(
                ref=new_ref,
                frontmatter=dict(inputs.semantic_entry_frontmatter),
                body=inputs.semantic_entry_body,
                updated_at=datetime.now(UTC),
            )
            self._by_ref[(new_ref.kind, new_ref.id)] = new_resource

            # 2. deliverable backref bump
            updated_fm = dict(current.frontmatter)
            backrefs = list(updated_fm.get("semantic_entries", []))
            backrefs.append(new_ref.id)
            updated_fm["semantic_entries"] = backrefs
            self._by_ref[src_key] = Resource(
                ref=current.ref,
                frontmatter=updated_fm,
                body=current.body,
                updated_at=datetime.now(UTC),
            )

            # 3. observation row
            obs_ref = ResourceRef(
                kind="observation",
                id=f"obs-{new_ref.id}",
                user_id=inputs.source_deliverable_ref.user_id,
                project_id=inputs.source_deliverable_ref.project_id,
            )
            self._by_ref[(obs_ref.kind, obs_ref.id)] = Resource(
                ref=obs_ref,
                frontmatter=dict(inputs.observation_payload),
                body="",
                updated_at=datetime.now(UTC),
            )

            return new_resource
