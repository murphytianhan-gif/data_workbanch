"""Clock injection for the engine.

The ReAct loop reads ``now`` through a Callable so tests can pin time
and produce byte-equal golden jsonl. Production code uses
``utc_now_rfc3339`` which is RFC3339 with millisecond precision and a
``Z`` suffix — matching the §8 wire schema for ``created_at``.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime


def utc_now_rfc3339() -> str:
    """RFC3339 UTC timestamp, millisecond precision, ``Z`` suffix."""
    now = datetime.now(UTC)
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}Z"


Clock = Callable[[], str]
"""Type alias for a no-arg function returning an RFC3339 timestamp."""
