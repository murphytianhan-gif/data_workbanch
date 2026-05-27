"""dclaw SQL tool — enforces §12.5 at the source.

Returns a ``ToolResult`` whose ``content`` is a human/LLM-readable
summary and whose ``raw`` payload contains *only* ``row_count``,
``sample_rows`` (max 200), and ``checks``. The raw row set is dropped
before it can ever reach storage; tests pin both halves of the §12.5
contract (engine truncation here + ``StorageProtocol.precipitate``
rejection on the data side).
"""
from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from typing import Any

from app.agent_engine.tool import Tool, ToolContext, ToolError, ToolResult

SAMPLE_ROW_CAP = 200


def _build_checks(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Per-column sanity checks: null count + distinct count.

    Best-effort; the tool's contract is that ``checks`` is a dict, not a
    fixed schema, so consumers should not depend on specific keys.
    """
    if not rows:
        return {"columns": []}
    cols = list(rows[0].keys())
    out: dict[str, Any] = {"columns": cols}
    for col in cols:
        values = [r.get(col) for r in rows]
        null_count = sum(1 for v in values if v is None)
        distinct = len({v for v in values if v is not None})
        out[col] = {"nulls": null_count, "distinct": distinct}
    return out


def truncate_sql_result(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """Pure helper — turn a raw row list into the §12.5 raw payload.

    Public so tests can pin the truncation contract without spinning up
    the whole Tool.
    """
    row_count = len(rows)
    sample = rows[:SAMPLE_ROW_CAP]
    return {
        "row_count": row_count,
        "sample_rows": sample,
        "checks": _build_checks(sample),
    }


SqlExecutor = Callable[[str, Mapping[str, Any]], Awaitable[list[dict[str, Any]]]]
"""Pluggable executor — usually backed by the dclaw MCP client."""


class DclawSqlTool(Tool):
    """The single LLM-callable surface for SQL execution."""

    name = "sql_query"
    description = (
        "Run a SQL query through dclaw. Returns row_count + up to 200 sample "
        "rows + per-column checks. Raw row sets are never persisted (§12.5)."
    )
    parameters_schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "sql": {"type": "string"},
            "params": {"type": "object", "additionalProperties": True},
        },
        "required": ["sql"],
    }

    def __init__(self, executor: SqlExecutor) -> None:
        self._executor = executor

    async def invoke(
        self,
        arguments: Mapping[str, Any],
        ctx: ToolContext,
    ) -> ToolResult:
        sql = str(arguments.get("sql", "")).strip()
        if not sql:
            return ToolResult(
                ok=False,
                content="missing 'sql' argument",
                error=ToolError(code="bad_argument", message="sql is required"),
            )
        params = dict(arguments.get("params") or {})
        try:
            rows = await self._executor(sql, params)
        except Exception as exc:  # network / driver failures
            return ToolResult(
                ok=False,
                content=f"sql execution failed: {exc}",
                error=ToolError(code="executor_error", message=str(exc)),
            )

        raw = truncate_sql_result(list(rows))
        content = (
            f"sql ok: row_count={raw['row_count']}, "
            f"sample_rows={len(raw['sample_rows'])} (cap={SAMPLE_ROW_CAP})"
        )
        return ToolResult(ok=True, content=content, raw=raw)
