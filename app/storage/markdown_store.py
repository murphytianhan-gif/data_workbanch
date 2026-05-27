"""Markdown-on-disk implementation of ``StorageProtocol``.

On-disk layout (from ``docs/contracts/data-model.md §3``)::

    <root>/
    ├── users/
    │   └── <user_id>/
    │       ├── agents/<id>.md                 # kind=agent, project_id=None
    │       └── projects/
    │           └── <project_id>/
    │               ├── project.md             # kind=project, id=<project_id>
    │               ├── frameworks/<id>.md
    │               ├── deliverables/<id>.md
    │               ├── references/<id>.md
    │               ├── sql/<id>.md
    │               ├── observations/<id>.md
    │               └── semantic/<id>.md       # kind=semantic_entry
    └── sessions/
        ├── <session_id>.md
        └── <session_id>.jsonl

Key invariants:

* System frontmatter keys (``id``, ``kind``, ``user_id``, ``project_id``,
  ``created_at``, ``updated_at``, ``deleted_at``) are owned by the storage
  layer; user-supplied frontmatter must not set them.
* Every mutating call carries ``if_updated_at`` and is OCC-checked against
  literal UTC microsecond ``datetime`` equality (``§7``).
* Writes go through ``_atomic_write_bytes`` (tmp + fsync + ``os.replace``),
  jsonl uses ``_append_bytes_fsync``.
* ``precipitate()`` is three-step atomic with reverse-order rollback
  (``§12``).
* ``§12.5``: any frontmatter list-of-dict field with ``len > 200`` is
  rejected with ``PrecipitateViolation``.
"""

from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from collections.abc import AsyncIterator, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import frontmatter  # type: ignore[import-not-found]
import yaml

from app.storage.protocol import (
    PrecipitateInputs,
    PrecipitateViolation,
    Resource,
    ResourceKind,
    ResourceNotFound,
    ResourceRef,
    StaleWriteError,
)

__all__ = ["MarkdownStore"]


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SYSTEM_FRONTMATTER_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "kind",
        "user_id",
        "project_id",
        "created_at",
        "updated_at",
        "deleted_at",
    }
)

# kind → directory name under projects/<pid>/ (or "" for project itself).
PROJECT_SCOPED_DIRS: dict[str, str] = {
    "framework": "frameworks",
    "deliverable": "deliverables",
    "reference": "references",
    "sql": "sql",
    "observation": "observations",
    "semantic_entry": "semantic",
}

USER_SCOPED_DIRS: dict[str, str] = {
    "agent": "agents",
}

RAW_ROWSET_THRESHOLD = 200  # > 200 → reject (§12.5)


# ---------------------------------------------------------------------------
# Time + ID helpers
# ---------------------------------------------------------------------------


def _utc_now() -> datetime:
    """``datetime.now(UTC)`` truncated to microsecond precision."""
    now = datetime.now(UTC)
    return now.replace(microsecond=now.microsecond)


def _fmt_ts(ts: datetime) -> str:
    """RFC3339 UTC microsecond with ``Z`` suffix."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    else:
        ts = ts.astimezone(UTC)
    base = ts.strftime("%Y-%m-%dT%H:%M:%S")
    return f"{base}.{ts.microsecond:06d}Z"


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    if isinstance(value, str):
        s = value.rstrip("Z")
        ts = datetime.fromisoformat(s)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts.astimezone(UTC)
    raise ValueError(f"unparseable timestamp: {value!r}")


def _uuid7() -> str:
    """Minimal UUIDv7 (RFC 9562 §5.7) — 48-bit ms timestamp + random."""
    ms = int(time.time() * 1000) & 0xFFFFFFFFFFFF
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)
    # version 7 in bits 48..51; variant 10 in bits 64..65.
    hi = (ms << 16) | (0x7 << 12) | rand_a
    lo = (0b10 << 62) | rand_b
    raw = hi.to_bytes(8, "big") + lo.to_bytes(8, "big")
    h = raw.hex()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


# ---------------------------------------------------------------------------
# Atomic write primitives
# ---------------------------------------------------------------------------


def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    """Write ``payload`` to ``path`` via tmp + fsync + ``os.replace``."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _append_bytes_fsync(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "ab") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())


# ---------------------------------------------------------------------------
# Frontmatter helpers
# ---------------------------------------------------------------------------


def _check_user_frontmatter(fm: Mapping[str, Any]) -> None:
    for key in fm:
        if key in SYSTEM_FRONTMATTER_KEYS:
            raise ValueError(f"system frontmatter key {key!r} reserved")


def _check_no_raw_rowset(fm: Mapping[str, Any]) -> None:
    """§12.5 — reject list-of-dict frontmatter values with ``len > 200``."""
    for key, value in fm.items():
        if isinstance(value, list) and len(value) > RAW_ROWSET_THRESHOLD:
            if all(isinstance(item, dict) for item in value):
                raise PrecipitateViolation(
                    f"§12.5 violation: raw rowset not persistable "
                    f"(field {key!r}, len={len(value)})"
                )


def _normalize_user_fm(fm: Mapping[str, Any] | None) -> dict[str, Any]:
    """Validate then copy user frontmatter into a fresh dict."""
    fm = fm or {}
    _check_user_frontmatter(fm)
    _check_no_raw_rowset(fm)
    return dict(fm)


def _system_fm(
    *,
    resource_id: str,
    kind: ResourceKind,
    user_id: str,
    project_id: str | None,
    created_at: datetime,
    updated_at: datetime,
    deleted_at: datetime | None,
) -> dict[str, Any]:
    return {
        "id": resource_id,
        "kind": kind,
        "user_id": user_id,
        "project_id": project_id,
        "created_at": _fmt_ts(created_at),
        "updated_at": _fmt_ts(updated_at),
        "deleted_at": _fmt_ts(deleted_at) if deleted_at else None,
    }


def _serialise(fm: Mapping[str, Any], body: str) -> bytes:
    """Render a YAML-frontmatter + body markdown file."""
    yaml_text = yaml.safe_dump(
        dict(fm),
        sort_keys=False,
        allow_unicode=True,
        default_flow_style=False,
    )
    return f"---\n{yaml_text}---\n{body}".encode()


def _load(path: Path) -> tuple[dict[str, Any], str]:
    """Read ``path`` → (frontmatter dict, body str)."""
    post = frontmatter.load(str(path))
    return dict(post.metadata), post.content


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------


class MarkdownStore:
    """File-backed ``StorageProtocol`` (single-process)."""

    def __init__(self, root: Path | str) -> None:
        self._root = Path(root)
        self._root.mkdir(parents=True, exist_ok=True)
        self._locks: dict[Path, asyncio.Lock] = {}
        self._locks_guard = asyncio.Lock()

    # ----- path math ------------------------------------------------------

    def _resource_path(self, ref: ResourceRef) -> Path:
        kind = ref.kind
        if kind == "session":
            return self._root / "sessions" / f"{ref.id}.md"
        if kind == "project":
            if ref.project_id is None:
                raise ValueError("project ref must carry project_id")
            return (
                self._root
                / "users"
                / ref.user_id
                / "projects"
                / ref.project_id
                / "project.md"
            )
        if kind in PROJECT_SCOPED_DIRS:
            if ref.project_id is None:
                raise ValueError(f"{kind!r} requires project_id")
            return (
                self._root
                / "users"
                / ref.user_id
                / "projects"
                / ref.project_id
                / PROJECT_SCOPED_DIRS[kind]
                / f"{ref.id}.md"
            )
        if kind in USER_SCOPED_DIRS:
            if ref.project_id is not None:
                raise ValueError(f"{kind!r} is user-scoped; project_id must be None")
            return (
                self._root
                / "users"
                / ref.user_id
                / USER_SCOPED_DIRS[kind]
                / f"{ref.id}.md"
            )
        raise ValueError(f"unknown kind: {kind!r}")

    def _list_dir(
        self, *, kind: ResourceKind, user_id: str, project_id: str | None
    ) -> Path:
        if kind == "session":
            return self._root / "sessions"
        if kind == "project":
            return self._root / "users" / user_id / "projects"
        if kind in PROJECT_SCOPED_DIRS:
            if project_id is None:
                raise ValueError(f"{kind!r} list requires project_id")
            return (
                self._root
                / "users"
                / user_id
                / "projects"
                / project_id
                / PROJECT_SCOPED_DIRS[kind]
            )
        if kind in USER_SCOPED_DIRS:
            return self._root / "users" / user_id / USER_SCOPED_DIRS[kind]
        raise ValueError(f"unknown kind: {kind!r}")

    def _session_jsonl(self, session_id: str) -> Path:
        return self._root / "sessions" / f"{session_id}.jsonl"

    # ----- lock acquisition ----------------------------------------------

    async def _lock_for(self, path: Path) -> asyncio.Lock:
        async with self._locks_guard:
            lock = self._locks.get(path)
            if lock is None:
                lock = asyncio.Lock()
                self._locks[path] = lock
            return lock

    # ----- single-resource CRUD ------------------------------------------

    async def get(self, ref: ResourceRef) -> Resource:
        path = self._resource_path(ref)
        if not path.exists():
            raise ResourceNotFound(f"{ref.kind}:{ref.id} not found")
        fm, body = _load(path)
        if fm.get("deleted_at"):
            raise ResourceNotFound(f"{ref.kind}:{ref.id} tombstoned")
        return _materialise(ref, fm, body)

    async def list(
        self,
        *,
        kind: ResourceKind,
        user_id: str,
        project_id: str | None = None,
    ) -> Sequence[ResourceRef]:
        if kind == "project":
            base = self._root / "users" / user_id / "projects"
            if not base.exists():
                return []
            out: list[ResourceRef] = []
            for child in sorted(base.iterdir()):
                pmd = child / "project.md"
                if not pmd.exists():
                    continue
                fm, _ = _load(pmd)
                if fm.get("deleted_at"):
                    continue
                out.append(
                    ResourceRef(
                        kind="project",
                        id=str(fm["id"]),
                        user_id=user_id,
                        project_id=child.name,
                    )
                )
            return out

        if kind == "session":
            base = self._root / "sessions"
            if not base.exists():
                return []
            out = []
            for p in sorted(base.glob("*.md")):
                fm, _ = _load(p)
                if fm.get("deleted_at"):
                    continue
                out.append(
                    ResourceRef(
                        kind="session",
                        id=str(fm.get("id", p.stem)),
                        user_id=str(fm.get("user_id", user_id)),
                        project_id=None,
                    )
                )
            return out

        d = self._list_dir(kind=kind, user_id=user_id, project_id=project_id)
        if not d.exists():
            return []
        out = []
        for p in sorted(d.glob("*.md")):
            fm, _ = _load(p)
            if fm.get("deleted_at"):
                continue
            out.append(
                ResourceRef(
                    kind=kind,
                    id=str(fm["id"]),
                    user_id=user_id,
                    project_id=project_id,
                )
            )
        return out

    async def create(
        self,
        ref: ResourceRef,
        frontmatter: Mapping[str, Any],
        body: str,
    ) -> Resource:
        user_fm = _normalize_user_fm(frontmatter)

        # For project / session the id from the caller is authoritative;
        # for everything else, assign a fresh UUIDv7 if the caller passed an
        # empty string.
        resource_id = ref.id if ref.id else _uuid7()
        ref = ResourceRef(
            kind=ref.kind, id=resource_id, user_id=ref.user_id, project_id=ref.project_id
        )

        path = self._resource_path(ref)
        lock = await self._lock_for(path)
        async with lock:
            if path.exists():
                raise FileExistsError(f"{ref.kind}:{ref.id} already exists at {path}")
            now = _utc_now()
            sys_fm = _system_fm(
                resource_id=resource_id,
                kind=ref.kind,
                user_id=ref.user_id,
                project_id=ref.project_id,
                created_at=now,
                updated_at=now,
                deleted_at=None,
            )
            full_fm = {**sys_fm, **user_fm}
            _atomic_write_bytes(path, _serialise(full_fm, body))
            return Resource(ref=ref, frontmatter=full_fm, body=body, updated_at=now)

    async def update(
        self,
        ref: ResourceRef,
        frontmatter: Mapping[str, Any],
        body: str,
        *,
        if_updated_at: datetime,
    ) -> Resource:
        user_fm = _normalize_user_fm(frontmatter)
        path = self._resource_path(ref)
        lock = await self._lock_for(path)
        async with lock:
            if not path.exists():
                raise ResourceNotFound(f"{ref.kind}:{ref.id} not found")
            existing_fm, _ = _load(path)
            if existing_fm.get("deleted_at"):
                raise ResourceNotFound(f"{ref.kind}:{ref.id} tombstoned")
            disk_updated = _parse_ts(existing_fm["updated_at"])
            if _normalise_occ(if_updated_at) != disk_updated:
                raise StaleWriteError(
                    f"OCC mismatch on {ref.kind}:{ref.id}: "
                    f"disk={disk_updated!s} vs if_updated_at={if_updated_at!s}"
                )
            now = _utc_now()
            sys_fm = _system_fm(
                resource_id=str(existing_fm["id"]),
                kind=ref.kind,
                user_id=str(existing_fm["user_id"]),
                project_id=existing_fm.get("project_id"),
                created_at=_parse_ts(existing_fm["created_at"]),
                updated_at=now,
                deleted_at=None,
            )
            full_fm = {**sys_fm, **user_fm}
            _atomic_write_bytes(path, _serialise(full_fm, body))
            return Resource(ref=ref, frontmatter=full_fm, body=body, updated_at=now)

    async def delete(
        self,
        ref: ResourceRef,
        *,
        if_updated_at: datetime,
    ) -> None:
        path = self._resource_path(ref)
        lock = await self._lock_for(path)
        async with lock:
            if not path.exists():
                raise ResourceNotFound(f"{ref.kind}:{ref.id} not found")
            existing_fm, _ = _load(path)
            disk_updated = _parse_ts(existing_fm["updated_at"])
            if _normalise_occ(if_updated_at) != disk_updated:
                raise StaleWriteError(
                    f"OCC mismatch on {ref.kind}:{ref.id} during delete"
                )
            if existing_fm.get("deleted_at"):
                return  # idempotent
            now = _utc_now()
            existing_fm["updated_at"] = _fmt_ts(now)
            existing_fm["deleted_at"] = _fmt_ts(now)
            _atomic_write_bytes(path, _serialise(existing_fm, ""))

    # ----- session jsonl --------------------------------------------------

    async def append_session_event(self, session_id: str, event_json: str) -> None:
        if "\n" in event_json:
            raise ValueError("embedded newline in session event")
        path = self._session_jsonl(session_id)
        lock = await self._lock_for(path)
        async with lock:
            _append_bytes_fsync(path, (event_json + "\n").encode("utf-8"))

    async def read_session_events(self, session_id: str) -> AsyncIterator[str]:
        path = self._session_jsonl(session_id)

        async def _iter() -> AsyncIterator[str]:
            if not path.exists():
                return
            with open(path, encoding="utf-8") as f:  # noqa: ASYNC230
                for line in f:
                    yield line.rstrip("\n")

        return _iter()

    # ----- precipitate ----------------------------------------------------

    async def precipitate(self, inputs: PrecipitateInputs) -> Resource:
        sem_user_fm = _normalize_user_fm(inputs.semantic_entry_frontmatter)
        obs_user_fm = _normalize_user_fm(inputs.observation_payload)
        if inputs.source_deliverable_ref.kind != "deliverable":
            raise ValueError(
                f"precipitate source must be deliverable, got "
                f"{inputs.source_deliverable_ref.kind!r}"
            )

        d_ref = inputs.source_deliverable_ref
        user_id = d_ref.user_id
        project_id = d_ref.project_id
        if project_id is None:
            raise ValueError("deliverable ref must carry project_id")

        sem_id = _uuid7()
        sem_ref = ResourceRef(
            kind="semantic_entry",
            id=sem_id,
            user_id=user_id,
            project_id=project_id,
        )
        obs_id = _uuid7()
        obs_ref = ResourceRef(
            kind="observation",
            id=obs_id,
            user_id=user_id,
            project_id=project_id,
        )

        sem_path = self._resource_path(sem_ref)
        d_path = self._resource_path(d_ref)
        obs_path = self._resource_path(obs_ref)

        # §11 — acquire locks in sorted str(path) order.
        paths_sorted = sorted({sem_path, d_path, obs_path}, key=lambda p: str(p))
        locks = [await self._lock_for(p) for p in paths_sorted]

        async with _ordered(locks):
            # OCC + load pre-write deliverable snapshot.
            if not d_path.exists():
                raise ResourceNotFound(f"deliverable:{d_ref.id} not found")
            d_fm_existing, d_body_existing = _load(d_path)
            if d_fm_existing.get("deleted_at"):
                raise ResourceNotFound(f"deliverable:{d_ref.id} tombstoned")
            disk_updated = _parse_ts(d_fm_existing["updated_at"])
            if _normalise_occ(inputs.source_deliverable_expected_updated_at) != disk_updated:
                raise StaleWriteError(
                    f"OCC mismatch on deliverable:{d_ref.id} during precipitate"
                )
            # Snapshot pre-write bytes for rollback (§12 step-2).
            d_pre_payload = _serialise(d_fm_existing, d_body_existing)

            rollbacks: list[Any] = []
            try:
                # Step 1 — create semantic_entry.
                now = _utc_now()
                sem_sys = _system_fm(
                    resource_id=sem_id,
                    kind="semantic_entry",
                    user_id=user_id,
                    project_id=project_id,
                    created_at=now,
                    updated_at=now,
                    deleted_at=None,
                )
                sem_fm = {**sem_sys, **sem_user_fm}
                _atomic_write_bytes(sem_path, _serialise(sem_fm, inputs.semantic_entry_body))
                rollbacks.append(("unlink", sem_path))

                # Step 2 — update deliverable's semantic_entries list.
                d_user_fm = {
                    k: v
                    for k, v in d_fm_existing.items()
                    if k not in SYSTEM_FRONTMATTER_KEYS
                }
                entries = list(d_user_fm.get("semantic_entries", []) or [])
                entries.append(sem_id)
                d_user_fm["semantic_entries"] = entries
                d_now = _utc_now()
                d_sys = _system_fm(
                    resource_id=str(d_fm_existing["id"]),
                    kind="deliverable",
                    user_id=user_id,
                    project_id=project_id,
                    created_at=_parse_ts(d_fm_existing["created_at"]),
                    updated_at=d_now,
                    deleted_at=None,
                )
                # Re-run §12.5 check on the post-merge frontmatter
                _check_no_raw_rowset(d_user_fm)
                d_full_fm = {**d_sys, **d_user_fm}
                _atomic_write_bytes(d_path, _serialise(d_full_fm, d_body_existing))
                rollbacks.append(("restore", d_path, d_pre_payload))

                # Step 3 — create observation file.
                obs_now = _utc_now()
                obs_sys = _system_fm(
                    resource_id=obs_id,
                    kind="observation",
                    user_id=user_id,
                    project_id=project_id,
                    created_at=obs_now,
                    updated_at=obs_now,
                    deleted_at=None,
                )
                obs_fm = {**obs_sys, **obs_user_fm}
                _atomic_write_bytes(obs_path, _serialise(obs_fm, ""))
                rollbacks.append(("unlink", obs_path))

                return Resource(
                    ref=sem_ref, frontmatter=sem_fm, body=inputs.semantic_entry_body, updated_at=now
                )
            except BaseException:
                _run_rollback(rollbacks)
                raise


def _run_rollback(rollbacks: list[Any]) -> None:
    """Execute rollback actions in reverse order, swallowing inner failures."""
    for action in reversed(rollbacks):
        try:
            kind = action[0]
            if kind == "unlink":
                p: Path = action[1]
                if p.exists():
                    p.unlink()
            elif kind == "restore":
                p = action[1]
                payload: bytes = action[2]
                _atomic_write_bytes(p, payload)
        except Exception:
            # Rollback failures must not mask the original exception.
            pass


def _materialise(
    ref: ResourceRef, fm: Mapping[str, Any], body: str
) -> Resource:
    return Resource(
        ref=ref,
        frontmatter=dict(fm),
        body=body,
        updated_at=_parse_ts(fm["updated_at"]),
    )


def _normalise_occ(ts: datetime) -> datetime:
    """Normalise an OCC-input timestamp the same way the writer stored it."""
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=UTC)
    return ts.astimezone(UTC)


# ---------------------------------------------------------------------------
# Multi-lock context manager (sorted acquire, reverse release)
# ---------------------------------------------------------------------------


class _ordered:
    """Acquire a list of locks in given order; release in reverse on exit."""

    def __init__(self, locks: Sequence[asyncio.Lock]) -> None:
        self._locks = list(locks)
        self._held: list[asyncio.Lock] = []

    async def __aenter__(self) -> _ordered:
        for lock in self._locks:
            await lock.acquire()
            self._held.append(lock)
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        while self._held:
            self._held.pop().release()


# ---------------------------------------------------------------------------
# Optional: a tiny convenience for tests that want to JSON-stringify a
# SessionEvent. Storage layer must NOT shape the event; we expose only a
# byte-identity guarantee.
# ---------------------------------------------------------------------------


def serialise_session_event(event: Mapping[str, Any]) -> str:
    """Produce the canonical ``<json>`` byte sequence for a SessionEvent.

    The session jsonl line is ``serialise_session_event(event) + "\\n"``.
    The SSE wire wraps the same string with ``data: ... \\n\\n``. Tests use
    this helper to build their golden fixtures, so both sides remain in
    lockstep.
    """
    return json.dumps(event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
