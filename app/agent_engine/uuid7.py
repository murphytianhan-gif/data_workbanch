"""Monotonic UUIDv7 generator.

Python 3.11 has no stdlib uuid7. The engine needs monotonically
non-decreasing ids so the jsonl ordering matches event emission order
even when two events share a millisecond.

Layout (per draft-ietf-uuidrev-rfc4122bis):
    48 bits  unix_ts_ms
     4 bits  version (= 7)
    12 bits  monotonic seq within ms
     2 bits  variant (= 0b10)
    62 bits  random
"""
from __future__ import annotations

import os
import threading
import time

_lock = threading.Lock()
_last_ms = 0
_last_seq = 0


def uuid7() -> str:
    """Return one UUIDv7 string. Monotonic across calls in this process."""
    global _last_ms, _last_seq
    with _lock:
        now_ms = int(time.time() * 1000)
        if now_ms <= _last_ms:
            now_ms = _last_ms
            _last_seq = (_last_seq + 1) & 0xFFF
            if _last_seq == 0:
                # 12-bit seq overflow within one ms — bump ms forward
                now_ms = _last_ms = _last_ms + 1
        else:
            _last_ms = now_ms
            _last_seq = 0
        ms = now_ms
        seq = _last_seq
    rand_b = int.from_bytes(os.urandom(8), "big") & ((1 << 62) - 1)
    n = (ms & 0xFFFFFFFFFFFF) << 80
    n |= 0x7 << 76
    n |= (seq & 0xFFF) << 64
    n |= 0x2 << 62
    n |= rand_b
    h = f"{n:032x}"
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"
