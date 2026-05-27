"""CRUD coverage for all eight resource kinds."""

from __future__ import annotations

import pytest

from app.storage.markdown_store import MarkdownStore
from app.storage.protocol import ResourceNotFound, ResourceRef


async def _create_project(
    store: MarkdownStore, user_id: str, project_id: str, title: str = "P0"
) -> None:
    await store.create(
        ResourceRef(kind="project", id=project_id, user_id=user_id, project_id=project_id),
        {"title": title, "description": "demo"},
        "project body",
    )


async def test_create_project(store: MarkdownStore, user_id: str, project_id: str) -> None:
    await _create_project(store, user_id, project_id)
    refs = await store.list(kind="project", user_id=user_id)
    assert [r.project_id for r in refs] == [project_id]
    r = await store.get(refs[0])
    assert r.frontmatter["title"] == "P0"
    assert r.frontmatter["kind"] == "project"
    assert r.body == "project body"


async def test_create_agent_user_scoped(store: MarkdownStore, user_id: str) -> None:
    ref = ResourceRef(kind="agent", id="", user_id=user_id, project_id=None)
    out = await store.create(ref, {"name": "A1", "model": "glm-4"}, "")
    assert out.frontmatter["kind"] == "agent"
    refs = await store.list(kind="agent", user_id=user_id)
    assert len(refs) == 1
    assert refs[0].id == out.ref.id


async def test_create_per_kind_files(store: MarkdownStore, user_id: str, project_id: str) -> None:
    await _create_project(store, user_id, project_id)
    for kind in ("framework", "deliverable", "reference", "sql", "observation", "semantic_entry"):
        out = await store.create(
            ResourceRef(kind=kind, id="", user_id=user_id, project_id=project_id),  # type: ignore[arg-type]
            {"title": f"{kind}-x"},
            f"{kind} body",
        )
        refs = await store.list(kind=kind, user_id=user_id, project_id=project_id)  # type: ignore[arg-type]
        assert [r.id for r in refs] == [out.ref.id]


async def test_system_frontmatter_keys_reserved(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    await _create_project(store, user_id, project_id)
    with pytest.raises(ValueError, match="reserved"):
        await store.create(
            ResourceRef(kind="deliverable", id="", user_id=user_id, project_id=project_id),
            {"title": "x", "id": "stolen"},
            "",
        )


async def test_update_bumps_updated_at(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    await _create_project(store, user_id, project_id)
    refs = await store.list(kind="project", user_id=user_id)
    cur = await store.get(refs[0])
    updated = await store.update(
        refs[0],
        {"title": "P1", "description": "after"},
        "new body",
        if_updated_at=cur.updated_at,
    )
    assert updated.frontmatter["title"] == "P1"
    assert updated.updated_at > cur.updated_at
    again = await store.get(refs[0])
    assert again.body == "new body"


async def test_delete_tombstones(store: MarkdownStore, user_id: str, project_id: str) -> None:
    await _create_project(store, user_id, project_id)
    refs = await store.list(kind="project", user_id=user_id)
    cur = await store.get(refs[0])
    await store.delete(refs[0], if_updated_at=cur.updated_at)
    assert await store.list(kind="project", user_id=user_id) == []
    with pytest.raises(ResourceNotFound):
        await store.get(refs[0])


async def test_delete_is_idempotent(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    await _create_project(store, user_id, project_id)
    refs = await store.list(kind="project", user_id=user_id)
    cur = await store.get(refs[0])
    await store.delete(refs[0], if_updated_at=cur.updated_at)
    # Second delete: read the tombstoned updated_at, expect no-op (returns None).
    from app.storage.markdown_store import _load, _parse_ts

    fm, _ = _load(store._resource_path(refs[0]))
    new_ts = _parse_ts(fm["updated_at"])
    await store.delete(refs[0], if_updated_at=new_ts)


async def test_get_missing_raises(store: MarkdownStore, user_id: str, project_id: str) -> None:
    with pytest.raises(ResourceNotFound):
        await store.get(
            ResourceRef(kind="deliverable", id="nope", user_id=user_id, project_id=project_id)
        )
