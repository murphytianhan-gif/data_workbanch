"""Agent engine — ReAct loop, Tool/Skill, GLM adapter, MCP transport.

Public surface (波次 1B — MUR-9):

* Protocols / dataclasses — ``llm``, ``tool``, ``skill``, ``react``.
* Implementations — ``engine.ReActLoopImpl``, ``skill_impl.SkillImpl``,
  ``registry.InMemoryToolRegistry``, ``glm.GLMClient``,
  ``sql_tools.DclawSqlTool``.
* Helpers — ``serialization`` (byte-stable JSON), ``sse_filter``
  (§12.3 expose_thinking), ``uuid7``, ``clock``.

See ``docs/contracts/agent-interface.md`` for the binding contract.
"""
