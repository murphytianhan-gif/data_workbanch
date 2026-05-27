"""``precipitate()`` happy path + step-2 / step-3 rollback."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import markdown_store
from app.storage.markdown_store import MarkdownStore
from app.storage.protocol import (
    PrecipitateInputs,
    PrecipitateViolation,
    ResourceRef,
)


async def _seed_project_and_deliverable(
    store: MarkdownStore, user_id: str, project_id: str
) -> ResourceRef:
    await store.create(
        ResourceRef(kind="project", id=project_id, user_id=user_id, project_id=project_id),
        {"title": "P"},
        "",
    )
    d_out = await store.create(
        ResourceRef(kind="deliverable", id="", user_id=user_id, project_id=project_id),
        {"title": "d", "type": "report", "semantic_entries": []},
        "deliverable body",
    )
    return d_out.ref


async def test_precipitate_happy_path(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    d_ref = await _seed_project_and_deliverable(store, user_id, project_id)
    d_cur = await store.get(d_ref)
    out = await store.precipitate(
        PrecipitateInputs(
            semantic_entry_frontmatter={
                "title": "insight",
                "summary": "first finding",
                "source_deliverable_id": d_ref.id,
                "tags": ["x"],
            },
            semantic_entry_body="semantic body",
            source_deliverable_ref=d_ref,
            source_deliverable_expected_updated_at=d_cur.updated_at,
            observation_payload={
                "timestamp": "2026-05-25T00:00:00.000000Z",
                "agent_id": "agent-x",
                "source_kind": "deliverable",
                "source_id": d_ref.id,
                "event": "precipitate",
            },
        )
    )
    assert out.ref.kind == "semantic_entry"
    # deliverable now has the new semantic_entries id
    d_after = await store.get(d_ref)
    assert out.ref.id in d_after.frontmatter["semantic_entries"]
    # observation file exists
    obs_refs = await store.list(
        kind="observation", user_id=user_id, project_id=project_id
    )
    assert len(obs_refs) == 1


async def test_precipitate_step2_rollback_restores_deliverable(
    store: MarkdownStore,
    user_id: str,
    project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d_ref = await _seed_project_and_deliverable(store, user_id, project_id)
    d_cur = await store.get(d_ref)
    d_path = store._resource_path(d_ref)
    pre_bytes = d_path.read_bytes()

    real_write = markdown_store._atomic_write_bytes
    calls = {"n": 0}

    def fail_on_deliverable(path: Path, payload: bytes) -> None:
        # Step 1 writes the semantic file (first write); step 2 writes the
        # deliverable (second write) — fail that one.
        calls["n"] += 1
        if calls["n"] == 2:
            assert path == d_path
            raise OSError("step-2 boom")
        real_write(path, payload)

    monkeypatch.setattr(markdown_store, "_atomic_write_bytes", fail_on_deliverable)
    with pytest.raises(OSError, match="step-2"):
        await store.precipitate(
            PrecipitateInputs(
                semantic_entry_frontmatter={"title": "i", "summary": "s"},
                semantic_entry_body="b",
                source_deliverable_ref=d_ref,
                source_deliverable_expected_updated_at=d_cur.updated_at,
                observation_payload={"event": "x"},
            )
        )
    # rollback: semantic file gone, deliverable untouched
    sem_dir = store._list_dir(
        kind="semantic_entry", user_id=user_id, project_id=project_id
    )
    assert not sem_dir.exists() or not list(sem_dir.glob("*.md"))
    assert d_path.read_bytes() == pre_bytes


async def test_precipitate_step3_rollback_removes_semantic_and_restores_deliverable(
    store: MarkdownStore,
    user_id: str,
    project_id: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    d_ref = await _seed_project_and_deliverable(store, user_id, project_id)
    d_cur = await store.get(d_ref)
    d_path = store._resource_path(d_ref)
    pre_bytes = d_path.read_bytes()

    real_write = markdown_store._atomic_write_bytes
    calls: dict[str, int] = {"n": 0}

    def fail_on_observation(path: Path, payload: bytes) -> None:
        calls["n"] += 1
        # Step 1 = semantic, Step 2 = deliverable, Step 3 = observation.
        if calls["n"] == 3:
            assert "observations" in str(path)
            raise OSError("step-3 boom")
        real_write(path, payload)

    monkeypatch.setattr(markdown_store, "_atomic_write_bytes", fail_on_observation)
    with pytest.raises(OSError, match="step-3"):
        await store.precipitate(
            PrecipitateInputs(
                semantic_entry_frontmatter={"title": "i"},
                semantic_entry_body="b",
                source_deliverable_ref=d_ref,
                source_deliverable_expected_updated_at=d_cur.updated_at,
                observation_payload={"event": "x"},
            )
        )
    # Step 1 (semantic) + Step 2 (deliverable) + Step 3 (observation, fails)
    # + rollback restore of deliverable = 4 calls.
    assert calls["n"] == 4
    # semantic file deleted
    sem_dir = store._list_dir(
        kind="semantic_entry", user_id=user_id, project_id=project_id
    )
    assert not sem_dir.exists() or not list(sem_dir.glob("*.md"))
    # deliverable byte-restored
    assert d_path.read_bytes() == pre_bytes
    # observation dir empty
    obs_refs = await store.list(
        kind="observation", user_id=user_id, project_id=project_id
    )
    assert obs_refs == []


async def test_precipitate_violation_raw_rowset_rejected(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    d_ref = await _seed_project_and_deliverable(store, user_id, project_id)
    d_cur = await store.get(d_ref)
    huge = [{"col": i} for i in range(201)]
    with pytest.raises(PrecipitateViolation):
        await store.precipitate(
            PrecipitateInputs(
                semantic_entry_frontmatter={"title": "i", "sample_rows": huge},
                semantic_entry_body="b",
                source_deliverable_ref=d_ref,
                source_deliverable_expected_updated_at=d_cur.updated_at,
                observation_payload={"event": "x"},
            )
        )


async def test_create_rejects_raw_rowset(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    await store.create(
        ResourceRef(kind="project", id=project_id, user_id=user_id, project_id=project_id),
        {"title": "P"},
        "",
    )
    huge = [{"col": i} for i in range(201)]
    with pytest.raises(PrecipitateViolation):
        await store.create(
            ResourceRef(kind="sql", id="", user_id=user_id, project_id=project_id),
            {"title": "q", "sample_rows": huge},
            "",
        )


async def test_create_allows_200_rowset(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    await store.create(
        ResourceRef(kind="project", id=project_id, user_id=user_id, project_id=project_id),
        {"title": "P"},
        "",
    )
    ok = [{"col": i} for i in range(200)]
    out = await store.create(
        ResourceRef(kind="sql", id="", user_id=user_id, project_id=project_id),
        {"title": "q", "sample_rows": ok},
        "",
    )
    assert len(out.frontmatter["sample_rows"]) == 200
