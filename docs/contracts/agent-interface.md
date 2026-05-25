# Agent Engine Interface Contract (波次 0 冻结版)

> **Status**: Frozen by Murphy 2026-05-25 (per MUR-5 §12 五项决议).
> **Owner**: 后端架构师 (`395242c4-1684-4af8-a470-28b627a384c4`).
> **Consumers**: 后端·引擎 (MUR-9), 后端·存储 (MUR-8 — only §11 mock seam + §8 SessionEvent jsonl format).
> **Change protocol**: any divergence requires a `[contract-change]` comment → Murphy 拍板 → architect updates the file. No silent edits in implementation PRs.

---

## §1 Scope

This document is the implementation contract for the agent engine — the in-process Python module that orchestrates a single ReAct loop, hosts Tool / Skill abstractions, and talks to one (configurable) LLM backend.

Out of scope (covered elsewhere):

- HTTP routing / SSE wire format → `openapi.yaml` (architect-owned, mirrors §8 `SessionEvent` byte-for-byte).
- Markdown file storage / 沉淀飞轮 / 观测落盘 → `docs/contracts/data-model.md` + `app/storage/protocol.py`.
- Workflow execution (multi-step deterministic orchestration) → MVP **不实现**, see §5.1 留位.

## §2 Type primitives and conventions

- Python 3.11, `from __future__ import annotations`, all public surfaces typed (`mypy --strict` clean).
- Public types are `Protocol` or `@dataclass(frozen=True)`; no Pydantic in the engine surface (storage layer may use it).
- All async; every IO-bound call returns `Awaitable[...]` or yields via `AsyncIterator[...]`.
- IDs are `str` UUIDv7 (created by storage layer, opaque to engine).
- Timestamps are `datetime` in UTC; serialised as RFC3339 with `Z` suffix on the wire.
- Money / large integers do not appear in engine surfaces; SQL results follow §12.5.

## §3 LLM adapter contract

The engine talks to exactly one `LLMClient` instance. Provider-agnostic on the surface; provider-specific knobs go through `extra: dict[str, Any]`.

```python
# app/agent_engine/llm.py
class ChatMessage(Protocol):
    role: Literal["system", "user", "assistant", "tool"]
    content: str
    tool_call_id: str | None        # required when role == "tool"
    tool_calls: list[ToolCallSpec] | None  # populated when assistant produced calls

class ToolDescriptor(Protocol):
    name: str
    description: str
    parameters: dict[str, Any]       # JSON Schema

class ToolCallSpec(Protocol):
    id: str
    name: str
    arguments: dict[str, Any]        # parsed (engine, not provider, owns JSON parsing)

class LLMResponse(Protocol):
    message: ChatMessage             # assistant turn
    finish_reason: Literal["stop", "tool_calls", "length", "error"]
    usage: TokenUsage | None

class LLMClient(Protocol):
    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDescriptor] | None = None,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,   # GLM-specific knobs go here
    ) -> LLMResponse: ...

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDescriptor] | None = None,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[LLMDelta]: ...
```

### §3.1 GLM gateway specifics (live behind the abstraction)

- HTTP transport: `httpx.AsyncClient`, base URL + token from env (`GLM_GATEWAY_URL`, `GLM_GATEWAY_TOKEN`).
- Streaming uses SSE with `data: {json}\n\n`; the engine consumes the parsed deltas, not raw bytes.
- Anything provider-specific (e.g. `thinking_budget`, `enable_thought`) flows through `extra`, never on the typed surface.

## §4 Tool protocol

A Tool is the atomic, LLM-callable unit. One call, one result, no internal loop.

```python
# app/agent_engine/tool.py
class ToolResult(Protocol):
    ok: bool
    content: str                 # what the LLM sees in the next turn
    raw: Any | None              # optional structured payload (engine-internal, e.g. for §12.5 SQL)
    error: ToolError | None      # populated iff not ok

class Tool(Protocol):
    name: str                    # unique within an Agent; matches LLM tool call name
    description: str
    parameters_schema: dict[str, Any]   # JSON Schema; emitted as ToolDescriptor.parameters

    async def invoke(
        self,
        arguments: Mapping[str, Any],
        ctx: ToolContext,
    ) -> ToolResult: ...

class ToolContext(Protocol):
    """Per-call context — never module-global; never imported across tools."""
    session_id: str
    agent_id: str
    user_id: str
    storage: StorageProtocol     # the seam — engine only imports the protocol type
    emit: Callable[[SessionEvent], Awaitable[None]]   # for tool-driven side events
```

### §4.1 Registry

```python
class ToolRegistry(Protocol):
    def register(self, tool: Tool) -> None: ...
    def get(self, name: str) -> Tool: ...           # raises ToolNotFound
    def all(self) -> Sequence[Tool]: ...
    def descriptors(self) -> Sequence[ToolDescriptor]: ...
```

- Registry is per-AgentDefinition; not a process-wide singleton.
- Tool names are case-sensitive, ASCII, max 64 chars.

## §5 Skill protocol

A Skill is a multi-step composite. Internally it runs a **mini ReAct** (see §6) against a scoped tool subset and its own system prompt; externally it exposes itself **as a single Tool** to the outer ReAct loop. This is the §12.1 decision.

```python
# app/agent_engine/skill.py
class Skill(Protocol):
    name: str
    description: str
    parameters_schema: dict[str, Any]    # what the outer LLM passes in
    inner_tools: Sequence[str]           # tool names the mini-ReAct may call
    inner_prompt: str                    # system prompt for the mini-ReAct
    inner_model: str | None              # may differ from outer agent's model
    inner_max_iters: int                 # cap on mini-ReAct iterations

    async def as_tool(self) -> Tool: ...  # adapter; the only way the outer loop sees a Skill
```

- A Skill MUST NOT call another Skill in the MVP (no nesting). Workflow留位 covers that case (§5.1).
- A Skill's mini-ReAct produces its own SessionEvent stream marked with `parent_tool_call_id`; the outer loop only sees one `tool_call` + `tool_result` pair.

### §5.1 Tool / Skill / Workflow / Agent boundary (§12.1 决议)

| 概念       | 定义                                                                  | MVP 状态                          |
| -------- | ------------------------------------------------------------------- | ------------------------------- |
| Tool     | 原子 LLM 工具；一次调用，一次返回。                                                | 实现                              |
| Skill    | 多步组合（内部 mini-ReAct）；**对外仍以「超级 Tool」形态喂给外层 LLM**。                    | 实现                              |
| Workflow | 确定性多步编排（DAG / 顺序步骤，可能含 `paused_for_human`）。                          | **不实现**（仅在 `agent-interface.md` 文字留位；不新增 schema/接口） |
| Agent    | 用户视角角色 = system prompt + 白名单（Tools ∪ Skills）+ model + `expose_thinking`。 | 实现                              |

## §6 ReAct loop

```
think  ->  (LLM produces tool_calls?) 
    yes -> dispatch each call (Tool.invoke / Skill.as_tool.invoke) -> feed tool results back -> think
    no  -> content -> done
```

```python
# app/agent_engine/react.py
class ReActLoop(Protocol):
    async def run(
        self,
        *,
        agent: AgentDefinition,
        user_input: str,
        session: SessionHandle,
    ) -> AsyncIterator[SessionEvent]: ...
```

- Max iterations bounded by `agent.max_iters` (default 12); exceeding emits a `SessionEvent{type=done, finish_reason="max_iters"}`.
- Tool dispatch is sequential per turn (parallel tool_calls in one assistant turn are dispatched concurrently with `asyncio.gather`, results collected before next think).
- The loop owns conversation state in-memory; persistence is delegated to storage via `emit` → jsonl append (see §8).

## §7 AgentDefinition

```python
@dataclass(frozen=True)
class AgentDefinition:
    id: str
    name: str
    system_prompt: str
    tools: Sequence[str]              # tool names enabled for this agent
    skills: Sequence[str]             # skill names enabled
    model: str                        # GLM model id
    temperature: float = 0.2
    max_tokens: int | None = None
    max_iters: int = 12
    expose_thinking: bool = True      # §12.3 decision
    extra: Mapping[str, Any] = field(default_factory=dict)
```

- `tools` and `skills` are *whitelists*; the engine refuses to dispatch anything not listed.
- `expose_thinking=False` (§12.3): `thinking` events MUST be persisted to jsonl, but MUST NOT be emitted to the outer SSE stream consumers. The filter sits in the SSE adapter, not in the ReAct loop — the loop always emits.

## §8 SessionEvent — push + jsonl byte-consistent

The wire format and the on-disk jsonl format are **the same bytes** (one JSON object per event; SSE wraps with `data: ...\n\n`, jsonl wraps with `\n`).

```python
# app/agent_engine/react.py (continued)
EventType = Literal["thinking", "tool_call", "tool_result", "content", "done", "error"]

@dataclass(frozen=True)
class SessionEvent:
    id: str                        # UUIDv7; monotonic for ordering
    session_id: str
    agent_id: str
    turn: int                      # 0-based turn within session
    iter: int                      # 0-based ReAct iter within turn
    type: EventType
    payload: dict[str, Any]        # type-dependent (see below)
    parent_tool_call_id: str | None = None
    created_at: str = field(default_factory=...)  # RFC3339 UTC with Z
```

### §8.1 Per-type payload schemas

| type          | payload keys                                          | notes |
| ------------- | ----------------------------------------------------- | ----- |
| `thinking`    | `text: str`                                           | omitted from SSE when `expose_thinking=false` |
| `tool_call`   | `tool_call_id: str, name: str, arguments: object`     | one event per call (parallel calls → multiple events same iter) |
| `tool_result` | `tool_call_id: str, ok: bool, content: str, raw?: object` | `raw` only set for §12.5 SQL results |
| `content`     | `text: str, delta: bool`                              | `delta=true` for streamed chunks, `delta=false` for finalized |
| `done`        | `finish_reason: "stop" \| "max_iters" \| "error"`     | always last event in stream |
| `error`       | `code: str, message: str`                             | transient; `done.finish_reason="error"` follows |

### §8.2 jsonl golden-file rule

Tests MUST compare engine output to a golden jsonl file. The CI guard:

```
diff <(engine_run --replay fixtures/scenario_X.jsonl) fixtures/scenario_X.golden.jsonl
```

byte-equal pass required. Any wire-format change is a `[contract-change]`.

## §9 MCP transport skeleton

MVP 范围：Cooper / dclaw 两套 MCP server 仅做 transport-层骨架，server 响应一律 mock。

```python
# app/mcp/client.py (skeleton, see app/agent_engine/tool.py for how MCP tools wrap)
class MCPClient(Protocol):
    server_id: str
    async def list_tools(self) -> Sequence[ToolDescriptor]: ...
    async def invoke(self, name: str, arguments: Mapping[str, Any]) -> ToolResult: ...
```

- Transport: HTTP + JSON-RPC 2.0 over `httpx.AsyncClient`.
- Auth: bearer token from env (`COOPER_MCP_TOKEN`, `DCLAW_MCP_TOKEN`).
- 真实 server 联通推到波次 1 末或波次 2。

## §10 Error / retry contract

- Provider 5xx / network: `tenacity` exponential backoff, 3 tries, max 8s — *only* at the LLM adapter and MCP client layers; never inside ReAct loop logic.
- Tool errors do not retry; they surface to the LLM as `tool_result{ok:false, content:"<error message visible to model>"}` so the model can self-correct.
- A `tool_call` whose tool name is not in `agent.tools ∪ agent.skills` → engine refuses, emits `tool_result{ok:false, content:"tool '<name>' not whitelisted for this agent"}`. The LLM may retry with a different tool.

## §11 Mock seam contracts (engine ↔ storage)

The engine SHALL NOT import any concrete storage module. It imports `app.storage.protocol.StorageProtocol` only.

For unit tests, the engine repo ships:

- `tests/_seams/inmemory_storage.py` — a dict + `asyncio.Lock` implementation of `StorageProtocol`. Preserves OCC + `precipitate()` atomicity *semantically* without touching the filesystem.
- `tests/_seams/mock_llm.py` — replays a scripted sequence of `ToolCall` / `Content` deltas. Used by every ReAct unit test.

CI guard: `import-linter` config in `pyproject.toml` forbids `app.agent_engine.* -> app.storage` (only `app.storage.protocol` allowed).

## §12 Frozen decisions (Murphy lock — 2026-05-25)

### §12.1 Tool / Skill / Workflow / Agent boundary

See §5.1 table. Skill = mini-ReAct adapted as super-Tool; Workflow MVP 不实现 (字面留位).

### §12.2 Workflow MVP scope

Out of scope. No schema, no endpoint, no execution path. Reserved name only.

### §12.3 `agent.expose_thinking`

Boolean field on `AgentDefinition`, default `true`. When `false`:

- `thinking` events MUST still be appended to the session jsonl (for observability + sediment).
- SSE adapter MUST filter out `thinking` events before they reach the SSE consumer.
- Filter location: SSE adapter only. The ReAct loop is unaware of this flag.

### §12.4 `X-User-Id` header

MVP accepts plain `X-User-Id: <uuid>` header on all API routes. No JWT in MVP. Production deployment: the company API gateway terminates JWT and re-writes the same header upstream; the backend code is identical in both modes.

### §12.5 SQL execution result handling

The dclaw SQL tool's `tool_result.raw` field carries:

```python
{
    "row_count": int,
    "sample_rows": list[dict],   # max 200 rows; truncated if larger
    "checks": dict,              # column-level sanity checks (nulls, distinct counts, etc.)
}
```

**The raw large result set MUST NOT be persisted to disk** anywhere — not in jsonl, not in storage layer, not in tool cache. A storage write that attempts to put a raw row set into a Deliverable frontmatter MUST be rejected with `ValueError("§12.5 violation: raw rowset not persistable")`. Storage layer (MUR-8) owns the rejection; engine layer (MUR-9) owns the truncation at the source. Test case (DoD #6 on MUR-9) MUST cover the rejection path.

---

## Appendix A: Public surface map

| Symbol                          | File                              |
| ------------------------------- | --------------------------------- |
| `ChatMessage`, `ToolDescriptor`, `LLMClient`, `LLMResponse`, `LLMDelta`, `TokenUsage` | `app/agent_engine/llm.py`         |
| `Tool`, `ToolResult`, `ToolError`, `ToolContext`, `ToolRegistry` | `app/agent_engine/tool.py`        |
| `Skill`                          | `app/agent_engine/skill.py`       |
| `ReActLoop`, `AgentDefinition`, `SessionEvent`, `SessionHandle`, `EventType` | `app/agent_engine/react.py`       |
| `StorageProtocol` (import only)  | `app/storage/protocol.py`         |
| `MCPClient`                      | `app/mcp/client.py`               |

## Appendix B: Versioning

This file is `v1.0` (frozen 2026-05-25). Bumps:

- `v1.x` — additive, backward-compatible (new fields with safe defaults, new event types ignored by older consumers).
- `v2.0` — breaking changes (requires `[contract-change]` + Murphy approval + parallel migration plan).
