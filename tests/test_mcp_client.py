"""MCP transport skeleton smoke test — see §9 + MUR-9 scope.

Server responses are mocked (httpx MockTransport). Pins JSON-RPC 2.0
envelope shape and the ToolResult unwrap path. Real Cooper / dclaw
server connectivity is out of scope for Wave 1B.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.mcp.client import HttpMCPClient


@pytest.mark.asyncio
async def test_list_tools_unwraps_jsonrpc_envelope():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["jsonrpc"] == "2.0"
        assert body["method"] == "tools/list"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "tools": [
                        {
                            "name": "search",
                            "description": "find docs",
                            "inputSchema": {"type": "object"},
                        }
                    ]
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = HttpMCPClient(
        server_id="cooper",
        base_url="https://cooper.example/mcp",
        token_env="COOPER_MCP_TOKEN",
        http_client=http,
    )
    tools = await client.list_tools()
    assert len(tools) == 1
    assert tools[0].name == "search"
    await http.aclose()


@pytest.mark.asyncio
async def test_invoke_unwraps_text_content():
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["method"] == "tools/call"
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": body["id"],
                "result": {
                    "content": [{"type": "text", "text": "found 3 hits"}],
                    "isError": False,
                },
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = HttpMCPClient(
        server_id="cooper",
        base_url="https://cooper.example/mcp",
        token_env="COOPER_MCP_TOKEN",
        http_client=http,
    )
    result = await client.invoke("search", {"q": "hi"})
    assert result.ok
    assert result.content == "found 3 hits"
    await http.aclose()
