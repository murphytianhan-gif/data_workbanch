"""MCP transport skeleton — see docs/contracts/agent-interface.md §9.

Wave 1B (MUR-9) ships the transport layer only. ``MCPClient`` is the
Protocol the engine talks to; ``HttpMCPClient`` is the concrete JSON-RPC
2.0 / httpx implementation that real Cooper / dclaw servers will
eventually answer to. Server responses are mocked in unit tests via an
injected ``httpx.AsyncClient`` (no ``transport=`` plumbing leaks into
the engine).

Real Cooper / dclaw server connectivity moves to the end of Wave 1 or
Wave 2 — see MUR-9 scope.
"""
from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.agent_engine.errors import MCPTransportError
from app.agent_engine.llm import ToolDescriptor
from app.agent_engine.tool import ToolError, ToolResult

_RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.HTTPStatusError,
)


class MCPClient(Protocol):
    server_id: str

    async def list_tools(self) -> Sequence[ToolDescriptor]: ...
    async def invoke(self, name: str, arguments: Mapping[str, Any]) -> ToolResult: ...


class HttpMCPClient:
    """JSON-RPC 2.0 over HTTP — production transport for Cooper / dclaw.

    Bearer token from env (``COOPER_MCP_TOKEN`` / ``DCLAW_MCP_TOKEN`` —
    pass the env var name in ``token_env``).
    """

    def __init__(
        self,
        *,
        server_id: str,
        base_url: str,
        token_env: str,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self.server_id = server_id
        self._base_url = base_url.rstrip("/")
        self._token = os.environ.get(token_env, "")
        self._http = http_client
        self._owns_http = http_client is None
        self._timeout = timeout
        self._req_id = 0

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _next_id(self) -> int:
        self._req_id += 1
        return self._req_id

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }

    async def _call(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        body = {
            "jsonrpc": "2.0",
            "id": self._next_id(),
            "method": method,
            "params": dict(params),
        }
        client = await self._client()

        async def do_call() -> httpx.Response:
            resp = await client.post(self._base_url, json=body, headers=self._headers())
            resp.raise_for_status()
            return resp

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(3),
                wait=wait_exponential(multiplier=0.5, max=8),
                retry=retry_if_exception_type(_RETRY_EXCEPTIONS),
                reraise=True,
            ):
                with attempt:
                    resp = await do_call()
        except _RETRY_EXCEPTIONS as exc:
            raise MCPTransportError(f"MCP {method} failed: {exc}") from exc

        envelope = resp.json()
        if "error" in envelope and envelope["error"]:
            err = envelope["error"]
            raise MCPTransportError(
                f"MCP {method} returned error {err.get('code')}: {err.get('message')}"
            )
        return dict(envelope.get("result") or {})

    async def list_tools(self) -> Sequence[ToolDescriptor]:
        result = await self._call("tools/list", {})
        descriptors: list[ToolDescriptor] = []
        for t in result.get("tools", []):
            descriptors.append(
                ToolDescriptor(
                    name=str(t.get("name", "")),
                    description=str(t.get("description", "")),
                    parameters=dict(t.get("inputSchema") or t.get("parameters") or {}),
                )
            )
        return descriptors

    async def invoke(self, name: str, arguments: Mapping[str, Any]) -> ToolResult:
        try:
            result = await self._call("tools/call", {"name": name, "arguments": dict(arguments)})
        except MCPTransportError as exc:
            return ToolResult(
                ok=False,
                content=f"mcp transport error: {exc}",
                error=ToolError(code="mcp_transport", message=str(exc), retriable=False),
            )
        content_chunks = result.get("content") or []
        parts: list[str] = []
        for chunk in content_chunks:
            if isinstance(chunk, dict) and chunk.get("type") == "text":
                parts.append(str(chunk.get("text", "")))
        return ToolResult(
            ok=not bool(result.get("isError")),
            content="\n".join(parts) if parts else "(empty result)",
            raw=result.get("structuredContent"),
        )
