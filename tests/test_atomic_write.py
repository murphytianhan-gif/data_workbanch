"""Crash-safety on the atomic-write primitive (§9)."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import markdown_store
from app.storage.markdown_store import MarkdownStore, _atomic_write_bytes
from app.storage.protocol import ResourceRef


def test_partial_write_leaves_old_file_or_new_file(tmp_path: Path) -> None:
    p = tmp_path / "x.md"
    _atomic_write_bytes(p, b"v1")
    assert p.read_bytes() == b"v1"
    _atomic_write_bytes(p, b"v2-fully-written")
    assert p.read_bytes() == b"v2-fully-written"


def test_crash_between_fsync_and_replace_leaves_tmp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    p = tmp_path / "x.md"
    _atomic_write_bytes(p, b"old")

    def boom(*_a: object, **_kw: object) -> None:
        raise OSError("simulated crash after fsync, before rename")

    monkeypatch.setattr(markdown_store.os, "replace", boom)
    with pytest.raises(OSError):
        _atomic_write_bytes(p, b"new")

    # Old file untouched; a .tmp file may exist on disk (best-effort cleanup).
    assert p.read_bytes() == b"old"
    tmps = list(tmp_path.glob("*.tmp"))
    assert len(tmps) == 1


async def test_create_then_update_atomic(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    await store.create(
        ResourceRef(kind="project", id=project_id, user_id=user_id, project_id=project_id),
        {"title": "P"},
        "v1",
    )
    refs = await store.list(kind="project", user_id=user_id)
    r = await store.get(refs[0])
    await store.update(refs[0], {"title": "P", "description": "x"}, "v2", if_updated_at=r.updated_at)
    again = await store.get(refs[0])
    assert again.body == "v2"
