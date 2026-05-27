"""End-to-end sample flow (data-model.md §13)."""

from __future__ import annotations

from app.storage.markdown_store import MarkdownStore, serialise_session_event
from app.storage.protocol import PrecipitateInputs, ResourceRef


async def test_sample_flow(store: MarkdownStore, user_id: str, project_id: str) -> None:
    # 1) project
    await store.create(
        ResourceRef(kind="project", id=project_id, user_id=user_id, project_id=project_id),
        {"title": "Demo project", "description": "e2e"},
        "project description in prose",
    )
    p_path = store._root / "users" / user_id / "projects" / project_id / "project.md"
    assert p_path.exists()

    # 2) deliverable with empty semantic_entries
    d_out = await store.create(
        ResourceRef(kind="deliverable", id="", user_id=user_id, project_id=project_id),
        {"title": "report", "type": "report", "status": "draft", "semantic_entries": []},
        "# Findings\nbody",
    )
    assert d_out.frontmatter["semantic_entries"] == []
    d_path = (
        store._root
        / "users"
        / user_id
        / "projects"
        / project_id
        / "deliverables"
        / f"{d_out.ref.id}.md"
    )
    assert d_path.exists()

    # 3) precipitate
    d_cur = await store.get(d_out.ref)
    sem = await store.precipitate(
        PrecipitateInputs(
            semantic_entry_frontmatter={
                "title": "key insight",
                "summary": "we saw X",
                "source_deliverable_id": d_out.ref.id,
                "tags": ["demo"],
            },
            semantic_entry_body="long-form insight",
            source_deliverable_ref=d_out.ref,
            source_deliverable_expected_updated_at=d_cur.updated_at,
            observation_payload={
                "timestamp": "2026-05-25T00:00:00.000000Z",
                "agent_id": "agent-1",
                "source_kind": "deliverable",
                "source_id": d_out.ref.id,
                "event": "precipitate",
            },
        )
    )
    sem_path = (
        store._root
        / "users"
        / user_id
        / "projects"
        / project_id
        / "semantic"
        / f"{sem.ref.id}.md"
    )
    assert sem_path.exists()
    d_after = await store.get(d_out.ref)
    assert sem.ref.id in d_after.frontmatter["semantic_entries"]
    obs_refs = await store.list(
        kind="observation", user_id=user_id, project_id=project_id
    )
    assert len(obs_refs) == 1

    # 4) session event
    sid = "01940000-0000-7000-8000-0000000000ff"
    event = {
        "id": "01940000-0000-7000-8000-00000000ee01",
        "session_id": sid,
        "agent_id": "agent-1",
        "turn": 0,
        "iter": 0,
        "type": "content",
        "payload": {"text": "hi", "delta": False},
        "parent_tool_call_id": None,
        "created_at": "2026-05-25T00:00:00.000000Z",
    }
    line = serialise_session_event(event)
    await store.append_session_event(sid, line)
    jsonl = store._root / "sessions" / f"{sid}.jsonl"
    assert jsonl.read_text(encoding="utf-8") == line + "\n"
