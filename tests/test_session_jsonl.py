"""Session jsonl byte-identity with ``agent-interface.md §8.1`` golden file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.storage.markdown_store import MarkdownStore, serialise_session_event

GOLDEN = Path(__file__).parent / "fixtures" / "session_events.golden.jsonl"


async def test_jsonl_bytes_match_golden(store: MarkdownStore) -> None:
    session_id = "01940000-0000-7000-8000-0000000000ff"
    golden_lines = GOLDEN.read_text(encoding="utf-8").splitlines()
    for line in golden_lines:
        event = json.loads(line)
        # canonical serialiser must round-trip byte-for-byte
        wire = serialise_session_event(event)
        assert wire == line
        await store.append_session_event(session_id, wire)

    on_disk = (store._root / "sessions" / f"{session_id}.jsonl").read_bytes()
    expected = "\n".join(golden_lines).encode("utf-8") + b"\n"
    assert on_disk == expected


async def test_append_rejects_embedded_newline(store: MarkdownStore) -> None:
    with pytest.raises(ValueError, match="embedded newline"):
        await store.append_session_event("sid", '{"a":"\nb"}')


async def test_golden_covers_all_event_types() -> None:
    types = set()
    for line in GOLDEN.read_text(encoding="utf-8").splitlines():
        types.add(json.loads(line)["type"])
    assert types == {"thinking", "tool_call", "tool_result", "content", "done"}


async def test_read_session_events_round_trips(store: MarkdownStore) -> None:
    session_id = "sid-1"
    await store.append_session_event(session_id, '{"type":"thinking","text":"a"}')
    await store.append_session_event(session_id, '{"type":"done","finish_reason":"stop"}')
    it = await store.read_session_events(session_id)
    lines = [line async for line in it]
    assert lines == [
        '{"type":"thinking","text":"a"}',
        '{"type":"done","finish_reason":"stop"}',
    ]
