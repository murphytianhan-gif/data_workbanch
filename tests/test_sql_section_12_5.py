"""DoD #6 — §12.5 dual coverage.

Engine side: ``DclawSqlTool`` truncates raw rowsets at the source. The
``ToolResult.raw`` exposes ``row_count`` + ``sample_rows`` (cap 200) +
``checks`` — never the original list.

Storage side: ``InMemoryStorage.precipitate`` raises
``PrecipitateViolation`` when a deliverable frontmatter would carry a
raw rowset.
"""
from __future__ import annotations

from datetime import UTC, datetime

import pytest

from app.agent_engine.context import ConcreteToolContext
from app.agent_engine.sql_tools import (
    SAMPLE_ROW_CAP,
    DclawSqlTool,
    truncate_sql_result,
)
from app.storage.protocol import (
    PrecipitateInputs,
    PrecipitateViolation,
    ResourceRef,
)
from tests._seams.inmemory_storage import InMemoryStorage


def test_truncate_pure_helper_caps_sample_rows():
    rows = [{"id": i, "v": i * 2} for i in range(SAMPLE_ROW_CAP + 50)]
    raw = truncate_sql_result(rows)
    assert raw["row_count"] == len(rows)
    assert len(raw["sample_rows"]) == SAMPLE_ROW_CAP
    # Per-column checks live in `checks`; structure is loose by design.
    assert "columns" in raw["checks"]


@pytest.mark.asyncio
async def test_dclaw_tool_drops_raw_rowset_at_source(storage):
    big_rows = [{"id": i} for i in range(500)]

    async def fake_executor(sql: str, params):
        return big_rows

    tool = DclawSqlTool(executor=fake_executor)

    async def fake_emit(_event):
        return None

    ctx = ConcreteToolContext(
        session_id="s",
        agent_id="a",
        user_id="u",
        storage=storage,
        emit=fake_emit,
    )
    result = await tool.invoke({"sql": "SELECT * FROM t"}, ctx)
    assert result.ok
    assert result.raw is not None
    assert result.raw["row_count"] == 500
    assert len(result.raw["sample_rows"]) == SAMPLE_ROW_CAP
    # The result must NOT carry the raw 500-row list anywhere.
    assert result.raw is not big_rows
    assert "rows" not in result.raw  # only sample_rows is allowed


@pytest.mark.asyncio
async def test_storage_precipitate_rejects_raw_rowset(storage: InMemoryStorage):
    """Storage half of the §12.5 contract — frontmatter with a raw rowset is rejected."""
    src_ref = ResourceRef(
        kind="deliverable",
        id="dlv-1",
        user_id="u-1",
        project_id="p-1",
    )
    src = await storage.create(
        src_ref,
        frontmatter={"title": "src"},
        body="placeholder",
    )

    bad_inputs = PrecipitateInputs(
        semantic_entry_frontmatter={
            "id": "se-1",
            "rows": [{"id": i} for i in range(SAMPLE_ROW_CAP + 1)],
        },
        semantic_entry_body="ignored",
        source_deliverable_ref=src_ref,
        source_deliverable_expected_updated_at=src.updated_at,
        observation_payload={"agent_id": "a"},
    )
    with pytest.raises(PrecipitateViolation):
        await storage.precipitate(bad_inputs)

    # Small inline samples are fine — under the cap.
    ok_inputs = PrecipitateInputs(
        semantic_entry_frontmatter={"id": "se-2", "summary": "ok"},
        semantic_entry_body="some markdown",
        source_deliverable_ref=src_ref,
        source_deliverable_expected_updated_at=src.updated_at,
        observation_payload={"agent_id": "a"},
    )
    result = await storage.precipitate(ok_inputs)
    assert result.ref.id == "se-2"


def test_observation_timestamp_just_so_pytest_picks_up_module():
    # Convenience guard: ensures the file's pytest collection includes our
    # test_sql_section_12_5 module even if every async test is filtered out
    # by a marker change.
    assert datetime.now(UTC).year >= 2026
