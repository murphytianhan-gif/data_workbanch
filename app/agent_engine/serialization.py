"""Byte-consistent SessionEvent serialization.

§8: SSE payload and on-disk jsonl share the same JSON bytes. SSE wraps
with ``data: <json>\\n\\n``, jsonl wraps with ``<json>\\n``.

Canonical form is ``json.dumps`` with
    sort_keys=True, ensure_ascii=False, separators=(",", ":")
so two encoders on different machines produce identical bytes.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any

from app.agent_engine.react import SessionEvent


def event_to_dict(event: SessionEvent) -> dict[str, Any]:
    """Stable dict view of a SessionEvent, ready for JSON encoding."""
    return asdict(event)


def encode_event(event: SessionEvent) -> str:
    """Canonical JSON for one SessionEvent (no trailing newline)."""
    return json.dumps(
        event_to_dict(event),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )


def to_sse_frame(json_line: str) -> str:
    """Wrap a canonical JSON line in an SSE ``data: ...\\n\\n`` frame."""
    return f"data: {json_line}\n\n"


def to_jsonl_line(json_line: str) -> str:
    """Wrap a canonical JSON line in jsonl form (trailing ``\\n``)."""
    return json_line + "\n"
