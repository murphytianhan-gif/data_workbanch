# Data Model Contract (波次 0 冻结版 + MUR-8 layout lock)

> **Status**: Frozen by Murphy 2026-05-25 (per MUR-5 §12 五项决议). Layout literals locked 2026-05-25 in MUR-8 §1–§4 after MUR-8 reasonable inference + 架构师 confirm.
> **Owner**: 后端架构师 (`395242c4-1684-4af8-a470-28b627a384c4`).
> **Consumers**: 后端·数据 (MUR-8 — implementation), 后端·引擎 (MUR-9 — only `StorageProtocol` type imports + §10 jsonl byte-consistency seam).
> **Change protocol**: any divergence requires `[contract-change]` comment → Murphy 拍板 → architect updates the file. No silent edits in implementation PRs.

---

## §1 Scope

This document is the implementation contract for the local-markdown storage layer — the on-disk layout, frontmatter schemas, write atomicity, OCC, tombstone, and the cross-file `precipitate()` operation. It pairs 1:1 with `app/storage/protocol.py` (the Python protocol surface) and with `docs/contracts/agent-interface.md §8` (the session jsonl byte-consistency rule).

Out of scope:

- LLM / Tool / Skill / ReAct semantics → `agent-interface.md`.
- HTTP routing / SSE wire format → `openapi.yaml`.
- Workflow execution → MVP 不实现 (字面留位, see `agent-interface.md §5.1`).

## §2 Type primitives and conventions

- All file content is UTF-8 text. Markdown files use YAML frontmatter delimited by `---` lines, body is markdown after the closing `---`.
- IDs are UUIDv7 strings (assigned by the storage layer at create time; opaque to the engine).
- Timestamps are `datetime` in UTC, microsecond precision, serialised in frontmatter / jsonl as RFC3339 with `Z` suffix (`2026-05-25T12:34:56.123456Z`).
- All paths inside this document use forward slashes; the implementation uses `pathlib.Path` and is OS-portable, but MVP assumes POSIX semantics for `os.replace` atomicity.

## §3 On-disk directory layout (LOCKED)

```
<root>/                                    # MarkdownStore root (e.g. "data/")
├── users/
│   └── <user_id>/
│       ├── agents/
│       │   └── <id>.md                    # kind=agent, project_id=None
│       └── projects/
│           └── <project_id>/
│               ├── project.md             # kind=project, id=<project_id>
│               ├── frameworks/
│               │   └── <id>.md
│               ├── deliverables/
│               │   └── <id>.md
│               ├── references/
│               │   └── <id>.md
│               ├── sql/
│               │   └── <id>.md
│               ├── observations/
│               │   └── <id>.md
│               └── semantic/
│                   └── <id>.md            # kind=semantic_entry
└── sessions/
    ├── <session_id>.md                    # session metadata (frontmatter + body)
    └── <session_id>.jsonl                 # append-only event stream (see §10)
```

Rules:

- **User-scoped resources** (`ResourceRef.project_id is None`) live under `users/<user_id>/<kind-plural>/`. MVP only has `kind=agent` here.
- **Project-scoped resources** (`ResourceRef.project_id` is set) live under `users/<user_id>/projects/<project_id>/<kind-plural>/<id>.md`.
- **The `project` kind is the exception**: a project's own metadata lives at `users/<user_id>/projects/<project_id>/project.md` (not nested under another `projects/` directory). The project directory itself is the resource's "home."
- **Sessions are global** (no user scope on the storage seam). `append_session_event(session_id, ...)` does not carry `user_id` by design; access control is enforced at the HTTP route layer via `X-User-Id`, not the storage layer. `sessions/` sits at the root.
- **Plural directory names**: `agents/`, `frameworks/`, `deliverables/`, `references/`, `sql/`, `observations/`, `semantic/` (semantic is already a non-count noun; no plural). The mapping `kind → directory` is fixed:

| `ResourceKind`   | Directory under `projects/<pid>/` (project-scoped) | Directory under `users/<user_id>/` (user-scoped) | Other          |
| ---------------- | -------------------------------------------------- | ------------------------------------------------ | -------------- |
| `project`        | `project.md` (single file, no subdir)              | —                                                | —              |
| `framework`      | `frameworks/<id>.md`                               | —                                                | —              |
| `deliverable`    | `deliverables/<id>.md`                             | —                                                | —              |
| `reference`      | `references/<id>.md`                               | —                                                | —              |
| `sql`            | `sql/<id>.md`                                      | —                                                | —              |
| `observation`    | `observations/<id>.md`                             | —                                                | —              |
| `semantic_entry` | `semantic/<id>.md`                                 | —                                                | —              |
| `agent`          | —                                                  | `agents/<id>.md`                                 | —              |
| `session`        | —                                                  | —                                                | `sessions/<session_id>.{md,jsonl}` |

## §4 Filename convention

Files are named `<id>.md` (UUIDv7 + `.md`). **No slug.** Reasons:

- `ResourceRef.id` is the stable identifier; the engine and tests address files by id, not by name.
- Slugs derived from human-readable titles would require filename-rename on title edits, which cascades to observation history and breaks log audits.
- Discoverability lives in frontmatter `title:` (per-kind, see §6), not in the filename. `list()` walks `glob('*.md')` and reads each frontmatter to surface titles/ids.

Special cases:

- `project.md` is the sole filename for project metadata (project_id is implicit from the parent directory name).
- `sessions/<session_id>.md` uses `session_id` directly (sessions have one logical id; no separate resource id).
- `sessions/<session_id>.jsonl` is the paired event-stream file (see §10).

## §5 Frontmatter — system fields (storage-owned, immutable to user)

Every markdown resource carries these keys at the top level of its YAML frontmatter. They are **written by the storage layer only**; a `create` / `update` / `precipitate` call whose user-supplied frontmatter tries to set any of these keys MUST be rejected with `ValueError("system frontmatter key <name> reserved")`.

| Key            | Type     | Notes                                                                              |
| -------------- | -------- | ---------------------------------------------------------------------------------- |
| `id`           | `str`    | UUIDv7 assigned at create time. Stable for the lifetime of the resource.           |
| `kind`         | `str`    | One of `ResourceKind` literal values (see §3 table).                               |
| `user_id`      | `str`    | Owning user. Matches `ResourceRef.user_id`.                                        |
| `project_id`   | `str?`   | Owning project (null for user-scoped resources).                                   |
| `created_at`   | RFC3339  | UTC, microsecond precision. Set on create; never changes.                          |
| `updated_at`   | RFC3339  | UTC, microsecond precision. Bumped on every `update` and on `delete` (tombstone). |
| `deleted_at`   | RFC3339? | Null while live; set to deletion timestamp when tombstoned. See §8.                |

`session.md` follows the same schema (with `kind="session"`, `user_id` set from the session's owning user — which the route layer passes in at session-create time; storage stores it but does not use it for lookup).

## §6 Per-kind frontmatter — user-extensible fields

Beyond the §5 system keys, each kind has a recommended set of frontmatter fields. These are **suggestions for the engine layer**; storage does not validate them (validation belongs in the engine's tool implementations). The MVP set:

| Kind             | Recommended frontmatter user keys                                                |
| ---------------- | -------------------------------------------------------------------------------- |
| `project`        | `title`, `description`                                                           |
| `framework`      | `title`, `description`                                                           |
| `deliverable`    | `title`, `type` (`"sql" \| "chart" \| "report" \| "raw_data" \| ...`), `sources` (`list[str]` of deliverable ids), `status` (`"draft" \| "human_reviewed" \| "promoted"`), `semantic_entries` (`list[str]` of semantic_entry ids — populated by `precipitate()`, see §12) |
| `reference`      | `title`, `source_url`, `summary`                                                 |
| `sql`            | `title`, `query`, `row_count`, `sample_rows` (`list[dict]`, ≤ 200 rows), `checks` (`dict`) — see §12.5 |
| `observation`    | `timestamp`, `agent_id`, `source_kind`, `source_id`, `event` (`str`)             |
| `semantic_entry` | `title`, `summary`, `source_deliverable_id`, `tags` (`list[str]`)                |
| `agent`          | `name`, `system_prompt`, `tools` (`list[str]`), `skills` (`list[str]`), `model`, `temperature`, `max_iters`, `expose_thinking` (see `agent-interface.md §12.3`) |
| `session`        | `title`, `agent_id`, `started_at`, `ended_at` (nullable)                         |

Body content: free-form markdown. Conventional usage — `project` body is the project description in prose; `deliverable` body is the produced artefact (a markdown report, an embedded chart description, etc.); `observation` body is empty or a short prose note.

## §7 Optimistic Concurrency Control (OCC)

Every mutating call (`update`, `delete`, `precipitate`) takes `if_updated_at: datetime`. The storage layer:

1. Reads the current `updated_at` from disk (parses frontmatter; does not need to read body).
2. Compares **literal equality** (UTC, microsecond-precision `datetime` objects). No tolerance window.
3. On mismatch, raises `StaleWriteError`. Disk is NOT touched.
4. On match, proceeds with the write, bumping `updated_at` to `datetime.now(UTC)` (truncated to microsecond) before persisting.

Notes:

- The `created_at` / `updated_at` precision MUST match Python's `datetime.now(UTC).replace(microsecond=...)` round-tripping — i.e. always microsecond, never nanosecond, never second.
- `get()` and `list()` never check OCC; reads are lock-free and may surface a slightly-stale view between a write and its `os.replace`. This is by design.
- `precipitate()` performs an OCC check on the source deliverable only (semantic entry is a fresh create; observation is append-only and has no OCC).

## §8 Tombstone delete

`delete(ref, if_updated_at=...)` does NOT remove the file. Instead:

1. OCC check (per §7).
2. Read existing frontmatter.
3. Set `deleted_at = datetime.now(UTC)` (microsecond precision).
4. Set `updated_at = deleted_at` (same value).
5. **Clear the body** to empty string.
6. Atomic write (§9) the resulting frontmatter+empty-body back to the same path.

Semantics:

- `get(ref)` on a tombstoned resource raises `ResourceNotFound`.
- `list(kind=...)` filters out tombstoned resources (rows with non-null `deleted_at`).
- Tombstone is idempotent: a second `delete()` on the same ref is a no-op if `deleted_at` is already set (still subject to OCC against `updated_at`).
- Tombstoned files are retained on disk for audit and recovery; MVP has no compaction job.

## §9 Atomic write

Single-file writes MUST use the temp-file + fsync + rename pattern:

```python
def _atomic_write_bytes(path: Path, payload: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(payload)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)        # atomic on POSIX
```

Guarantees:

- A reader at any point sees either the previous full file or the new full file; never a partial write.
- A crash between `os.fsync` and `os.replace` leaves `<path>` unchanged and a stray `<path>.tmp` on disk; the next write overwrites the tmp. Cleanup of stale `.tmp` files is best-effort at startup; MVP does not require it.
- The temp file's directory MUST be the same as the final path (so `os.replace` is on the same filesystem).

`jsonl` appends use a different primitive (`_append_bytes_fsync`): open in append mode, write the line, `fsync`. Append-mode writes of single complete lines are atomic on POSIX up to `PIPE_BUF` (well above one JSON event). For events larger than `PIPE_BUF`, the storage layer SHOULD chunk into the buffer with explicit serialization — MVP `SessionEvent` payloads are bounded well below this in practice.

## §10 Session jsonl — byte-consistent with `agent-interface.md §8`

The `sessions/<session_id>.jsonl` file is append-only. Each line is exactly `<json>\n` where `<json>` is the same byte sequence the SSE adapter emits as the data: payload (between `data: ` and `\n\n`). See `agent-interface.md §8` and `§8.1` for the per-type payload schemas.

Rules:

- `append_session_event(session_id, event_json)` MUST reject any `event_json` containing an embedded `\n` byte before serialisation (raise `ValueError("embedded newline in session event")`). The caller (engine) is responsible for JSON serialising with `ensure_ascii=False` and no internal newlines.
- Tests MUST byte-compare the on-disk jsonl against the SSE wire bytes (golden file diff). See `agent-interface.md §8.2`.
- A session may have a `.md` partner file (metadata) — the `.jsonl` carries the event stream and has no frontmatter; the `.md` carries the session frontmatter only and (typically) an empty body.

## §11 Concurrency — per-path async lock

`MarkdownStore` holds an internal `dict[Path, asyncio.Lock]`. Every mutating call acquires the lock for its target path. Reads do not acquire locks.

Multi-path operations (`precipitate()`) MUST:

1. Compute the set of paths they will touch.
2. **Sort the paths by `str(path)` lexicographically.**
3. Acquire locks in sorted order.
4. Release in reverse order (Python's `async with` stack handles this).

Sorted acquisition is what prevents the obvious deadlock when two `precipitate()` calls overlap on different deliverables but the same observation file. MVP single-process is the only target; multi-process / multi-machine coordination is out of scope (see `README.md` deployment note).

## §12 `precipitate()` — atomic three-file 沉淀飞轮

Semantics from `app/storage/protocol.py::PrecipitateInputs`:

1. **Step 1 — Create semantic_entry** at `users/<u>/projects/<p>/semantic/<new_id>.md`. New file; no OCC.
2. **Step 2 — Write deliverable backref**: load `users/<u>/projects/<p>/deliverables/<d>.md`, OCC check against `source_deliverable_expected_updated_at`, append `<new_id>` to its frontmatter `semantic_entries` list, atomic-write the whole resource back.
3. **Step 3 — Append observation**: append a new line to `users/<u>/projects/<p>/observations/<obs_id>.md` (or create it if absent — MVP convention: one observation file per UUIDv7, append-only frontmatter+body). Alternative MVP-acceptable: append a new `observation` resource file at `observations/<new_obs_id>.md`. Either form satisfies §12 atomicity.

On any step's failure, the storage layer MUST run a rollback list **in reverse order**:

- Step 1 rollback: delete the created semantic file.
- Step 2 rollback: restore the deliverable to its pre-step-2 frontmatter+body byte-for-byte (the storage layer snapshots the pre-write payload in memory before acquiring the deliverable lock; rollback writes that snapshot back via the same atomic-write primitive).
- Step 3 rollback: delete the just-appended observation file (or roll back the append if the chosen form is append-to-existing).

Rollback may itself fail (disk full, permission flap, etc.). Rollback failures are logged and swallowed; they do NOT mask the original exception. The original exception (from the failing step) is the one propagated to the engine. Storage layer test coverage MUST include both step-2-fails and step-3-fails rollback paths, asserting on-disk state matches pre-`precipitate` state.

### §12.5 Raw rowset rejection (mirror of `agent-interface.md §12.5`)

The storage layer MUST reject any `create` / `update` / `precipitate` whose frontmatter contains a list-of-dict field with length > 200, raising `PrecipitateViolation` (= `ValueError` subclass). This is the **storage-side guardrail** of the §12.5 双保险; the engine-side counterpart truncates at the dclaw tool result construction. Both layers MUST cover the rejection path in tests.

Notes:

- The rule applies to any field, not just `sample_rows` — a deliverable frontmatter accidentally carrying a raw query result would also be rejected.
- The threshold is exactly `> 200`; lists of length 200 are allowed.
- The check runs **before** any disk write (OCC and the violation check are both fast-path gates).

## §13 Sample directory walkthrough

A minimal valid `<root>/` after one project + one deliverable + one precipitate + one session:

```
data/
├── users/
│   └── 0193e8a0-7a4f-7000-8b1c-1234567890ab/
│       ├── agents/
│       │   └── 0193e8b0-1a4f-7000-8b1c-aaaaaaaaaaaa.md
│       └── projects/
│           └── 0193e8c0-2a4f-7000-8b1c-bbbbbbbbbbbb/
│               ├── project.md
│               ├── deliverables/
│               │   └── 0193e8d0-3a4f-7000-8b1c-cccccccccccc.md   # frontmatter.semantic_entries=[0193e8e0-...]
│               ├── observations/
│               │   └── 0193e8f0-4a4f-7000-8b1c-eeeeeeeeeeee.md
│               └── semantic/
│                   └── 0193e8e0-5a4f-7000-8b1c-dddddddddddd.md
└── sessions/
    ├── 0193e900-6a4f-7000-8b1c-ffffffffffff.md
    └── 0193e900-6a4f-7000-8b1c-ffffffffffff.jsonl
```

The MUR-8 DoD #6 (`test_e2e.py::test_sample_flow`) MUST exercise:

1. `create` a `project` → `project.md` appears.
2. `create` a `deliverable` with prose body → file appears with `semantic_entries=[]` initially.
3. `precipitate` against that deliverable → semantic file created, deliverable backref written, observation file appended; all three are visible after the call returns.
4. `append_session_event` for the session → jsonl gains one line, byte-matching `agent-interface.md §8.1` for each event type.

## §14 Frozen decisions (Murphy lock — 2026-05-25)

The data-model concerns under the §12 五项决议 (see `agent-interface.md §12`) reflected here:

- **§12.3 `expose_thinking`**: storage just persists every `thinking` event to the jsonl. The SSE filter sits in the route layer; the storage layer is unaware of the flag.
- **§12.4 `X-User-Id`**: storage layer accepts the `user_id` parameter from the route layer at face value; no JWT, no token check.
- **§12.5 raw rowset**: storage-side guardrail per §12.5 above.

The directory-layout literals and filename convention (§3 + §4) are locked **2026-05-25** following the MUR-8 inference + architect ratification. Subsequent edits go through the `[contract-change]` flow.

---

## Appendix A: Public surface map

| Symbol                          | File                              |
| ------------------------------- | --------------------------------- |
| `ResourceKind`, `ResourceRef`, `Resource`, `StaleWriteError`, `ResourceNotFound`, `PrecipitateViolation`, `PrecipitateInputs`, `StorageProtocol` | `app/storage/protocol.py` |
| `MarkdownStore` (concrete impl) | `app/storage/markdown_store.py` (MUR-8 deliverable) |

## Appendix B: Versioning

This file is `v1.0` (frozen 2026-05-25). Bumps:

- `v1.x` — additive, backward-compatible (new kinds, new optional frontmatter user-keys, additional event types ignored by older consumers).
- `v2.0` — breaking changes (directory rename, system-frontmatter rename, OCC semantic change). Requires `[contract-change]` + Murphy approval + parallel migration plan.
