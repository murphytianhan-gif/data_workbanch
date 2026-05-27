"""DoD #2 — GLM adapter wires the gateway transport end-to-end.

We do not hit the real gateway in CI; httpx ``MockTransport`` returns a
canned OpenAI-shaped completion. The test pins the request body shape
(messages, tools, extra) and the response parsing path.
"""
from __future__ import annotations

import json

import httpx
import pytest

from app.agent_engine.glm import GLMClient
from app.agent_engine.llm import ChatMessage, ToolCallSpec, ToolDescriptor


@pytest.mark.asyncio
async def test_glm_chat_hello_world():
    captured: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = dict(request.headers)
        captured["body"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "hello back",
                            "tool_calls": None,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
            },
        )

    transport = httpx.MockTransport(handler)
    http = httpx.AsyncClient(transport=transport, base_url="https://glm.gateway")
    client = GLMClient(base_url="https://glm.gateway", token="t0k3n", http_client=http)

    resp = await client.chat(
        [ChatMessage(role="user", content="hi")],
        tools=None,
        model="glm-4",
        temperature=0.0,
        extra={"thinking_budget": 16},
    )

    assert resp.finish_reason == "stop"
    assert resp.message.content == "hello back"
    assert resp.usage is not None
    assert resp.usage.total_tokens == 10

    body = captured["body"]
    assert body["model"] == "glm-4"
    assert body["temperature"] == 0.0
    assert body["thinking_budget"] == 16  # extra merged in
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert captured["headers"]["authorization"] == "Bearer t0k3n"

    await http.aclose()


@pytest.mark.asyncio
async def test_glm_parses_tool_calls():
    """Tool calls in the assistant turn arrive parsed (not raw JSON strings)."""

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "let me query",
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "sql_query",
                                        "arguments": json.dumps(
                                            {"sql": "SELECT 1", "params": {"k": 2}}
                                        ),
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ]
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GLMClient(base_url="https://glm.gateway", token="t", http_client=http)
    resp = await client.chat(
        [ChatMessage(role="user", content="run it")],
        tools=[
            ToolDescriptor(
                name="sql_query", description="run SQL", parameters={"type": "object"}
            )
        ],
        model="glm-4",
    )
    assert resp.finish_reason == "tool_calls"
    assert resp.message.tool_calls is not None
    call: ToolCallSpec = resp.message.tool_calls[0]
    assert call.id == "call_abc"
    assert call.name == "sql_query"
    assert call.arguments == {"sql": "SELECT 1", "params": {"k": 2}}
    await http.aclose()


@pytest.mark.asyncio
async def test_glm_retries_on_5xx():
    """Tenacity retries — 2 × 503 then 200, three total attempts."""
    attempts = {"n": 0}

    def handler(_: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503, text="busy")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"role": "assistant", "content": "ok"},
                        "finish_reason": "stop",
                    }
                ]
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = GLMClient(base_url="https://glm.gateway", token="t", http_client=http)
    resp = await client.chat(
        [ChatMessage(role="user", content="hi")], tools=None, model="glm-4"
    )
    assert resp.message.content == "ok"
    assert attempts["n"] == 3
    await http.aclose()
