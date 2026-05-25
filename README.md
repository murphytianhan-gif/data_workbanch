# Analyst AI Workbench — Backend Scaffold (wave 0)

> **Frozen contract** — Murphy locked the wave-0 contracts on 2026-05-25.
> See `docs/contracts/agent-interface.md` for the engine surface.
> See `docs/contracts/data-model.md` for the markdown storage layout (TBD distribution).
> See `docs/contracts/openapi.yaml` for the HTTP surface (TBD distribution).

This bundle is the **engine-critical slice** of the wave-0 scaffold,
distributed via issue attachment because Multica agent worktrees do not
share filesystem state. A shared git URL (preferred long-term path) is
pending Murphy's decision — see MUR-9 thread.

## Layout

```
app/
  agent_engine/    typing protocols only (§3-§8)
    llm.py
    tool.py
    skill.py
    react.py
  storage/
    protocol.py    StorageProtocol — engine import target (no impl here)
  mcp/
    client.py      MCP transport skeleton
docs/contracts/
  agent-interface.md
pyproject.toml     deps + ruff/mypy/pytest/import-linter config
```

## Run / lint / test

```
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"

ruff check app tests
mypy app
pytest -q
lint-imports                  # import-linter; enforces §11 seam
```

The engine implementation (MUR-9) replaces the protocol stubs with concrete
classes; the protocol surface stays stable barring a `[contract-change]`.
