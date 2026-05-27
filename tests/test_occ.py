"""OCC — stale ``if_updated_at`` raises ``StaleWriteError``."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.storage.markdown_store import MarkdownStore
from app.storage.protocol import ResourceRef, StaleWriteError


async def _seed(store: MarkdownStore, user_id: str, project_id: str) -> ResourceRef:
    await store.create(
        ResourceRef(kind="project", id=project_id, user_id=user_id, project_id=project_id),
        {"title": "P"},
        "",
    )
    ref = ResourceRef(kind="deliverable", id="", user_id=user_id, project_id=project_id)
    out = await store.create(ref, {"title": "d"}, "body")
    return out.ref


async def test_update_stale_if_updated_at_raises(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    ref = await _seed(store, user_id, project_id)
    bogus = datetime(2020, 1, 1, tzinfo=UTC)
    with pytest.raises(StaleWriteError):
        await store.update(ref, {"title": "x"}, "b", if_updated_at=bogus)


async def test_update_then_second_update_with_old_ts_raises(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    ref = await _seed(store, user_id, project_id)
    cur = await store.get(ref)
    first_ts = cur.updated_at
    await store.update(ref, {"title": "v2"}, "b2", if_updated_at=first_ts)
    with pytest.raises(StaleWriteError):
        await store.update(ref, {"title": "v3"}, "b3", if_updated_at=first_ts)


async def test_delete_stale_raises(
    store: MarkdownStore, user_id: str, project_id: str
) -> None:
    ref = await _seed(store, user_id, project_id)
    cur = await store.get(ref)
    bogus = cur.updated_at - timedelta(seconds=1)
    with pytest.raises(StaleWriteError):
        await store.delete(ref, if_updated_at=bogus)
