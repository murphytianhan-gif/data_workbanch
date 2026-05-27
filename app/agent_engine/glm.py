"""GLM gateway adapter — concrete ``LLMClient`` over the company gateway.

§3.1 — transport is HTTP + SSE over ``httpx.AsyncClient``. Body schema
is OpenAI-compatible (chat completions). GLM-specific knobs flow
through ``extra`` (e.g. ``thinking_budget``, ``enable_thought``); they
do not appear on the typed surface.

Retries: tenacity, 3 attempts, max 8s — §10. Tool errors are NOT
retried at this layer; only network / 5xx are.
"""
from __future__ import annotations

import json
import os
from collections.abc import AsyncIterator, Mapping, Sequence
from typing import Any

import httpx
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

from app.agent_engine.errors import LLMTransportError
from app.agent_engine.llm import (
    ChatMessage,
    LLMDelta,
    LLMResponse,
    TokenUsage,
    ToolCallSpec,
    ToolDescriptor,
)

_RETRY_EXCEPTIONS: tuple[type[Exception], ...] = (
    httpx.ConnectError,
    httpx.ReadTimeout,
    httpx.RemoteProtocolError,
    httpx.HTTPStatusError,
)


def _build_messages(messages: Sequence[ChatMessage]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for m in messages:
        msg: dict[str, Any] = {"role": m.role, "content": m.content}
        if m.tool_call_id is not None:
            msg["tool_call_id"] = m.tool_call_id
        if m.tool_calls is not None:
            msg["tool_calls"] = [
                {
                    "id": c.id,
                    "type": "function",
                    "function": {
                        "name": c.name,
                        "arguments": json.dumps(c.arguments, ensure_ascii=False),
                    },
                }
                for c in m.tool_calls
            ]
        out.append(msg)
    return out


def _build_tools(tools: Sequence[ToolDescriptor] | None) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


def _parse_tool_calls(raw: list[dict[str, Any]] | None) -> tuple[ToolCallSpec, ...] | None:
    if not raw:
        return None
    parsed: list[ToolCallSpec] = []
    for c in raw:
        fn = c.get("function") or {}
        raw_args = fn.get("arguments", "{}")
        if isinstance(raw_args, str):
            try:
                args = json.loads(raw_args) if raw_args else {}
            except json.JSONDecodeError:
                args = {}
        else:
            args = dict(raw_args)
        parsed.append(
            ToolCallSpec(id=str(c.get("id", "")), name=str(fn.get("name", "")), arguments=args)
        )
    return tuple(parsed)


def _parse_response(body: dict[str, Any]) -> LLMResponse:
    choice = (body.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    tool_calls = _parse_tool_calls(msg.get("tool_calls"))
    finish_reason = str(choice.get("finish_reason") or "stop")
    if finish_reason not in ("stop", "tool_calls", "length", "error"):
        finish_reason = "error"
    chat_msg = ChatMessage(
        role="assistant",
        content=str(msg.get("content") or ""),
        tool_calls=tool_calls,
    )
    usage_raw = body.get("usage")
    usage = None
    if usage_raw:
        usage = TokenUsage(
            prompt_tokens=int(usage_raw.get("prompt_tokens", 0)),
            completion_tokens=int(usage_raw.get("completion_tokens", 0)),
            total_tokens=int(usage_raw.get("total_tokens", 0)),
        )
    return LLMResponse(message=chat_msg, finish_reason=finish_reason, usage=usage)  # type: ignore[arg-type]


class GLMClient:
    """Concrete LLMClient backed by the GLM gateway."""

    def __init__(
        self,
        *,
        base_url: str | None = None,
        token: str | None = None,
        http_client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base_url = (base_url or os.environ.get("GLM_GATEWAY_URL", "")).rstrip("/")
        self._token = token or os.environ.get("GLM_GATEWAY_TOKEN", "")
        self._timeout = timeout
        self._http = http_client  # injected for tests; otherwise lazily created
        self._owns_http = http_client is None

    async def _client(self) -> httpx.AsyncClient:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout)
        return self._http

    async def aclose(self) -> None:
        if self._owns_http and self._http is not None:
            await self._http.aclose()
            self._http = None

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    def _build_body(
        self,
        *,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDescriptor] | None,
        model: str,
        temperature: float,
        max_tokens: int | None,
        stream: bool,
        extra: Mapping[str, Any] | None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": _build_messages(messages),
            "temperature": temperature,
            "stream": stream,
        }
        tools_body = _build_tools(tools)
        if tools_body is not None:
            body["tools"] = tools_body
            body["tool_choice"] = "auto"
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        if extra:
            for k, v in extra.items():
                body[k] = v
        return body

    async def chat(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDescriptor] | None = None,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> LLMResponse:
        body = self._build_body(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            extra=extra,
        )
        url = f"{self._base_url}/chat/completions" if self._base_url else "/chat/completions"
        client = await self._client()

        async def do_call() -> httpx.Response:
            resp = await client.post(url, json=body, headers=self._headers())
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
            raise LLMTransportError(f"GLM chat failed: {exc}") from exc

        return _parse_response(resp.json())

    async def stream(
        self,
        messages: Sequence[ChatMessage],
        tools: Sequence[ToolDescriptor] | None = None,
        *,
        model: str,
        temperature: float = 0.2,
        max_tokens: int | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> AsyncIterator[LLMDelta]:
        body = self._build_body(
            messages=messages,
            tools=tools,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            extra=extra,
        )
        url = f"{self._base_url}/chat/completions" if self._base_url else "/chat/completions"
        client = await self._client()

        async def gen() -> AsyncIterator[LLMDelta]:
            try:
                async with client.stream("POST", url, json=body, headers=self._headers()) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line or not line.startswith("data:"):
                            continue
                        payload = line[len("data:") :].strip()
                        if payload == "[DONE]":
                            break
                        try:
                            chunk = json.loads(payload)
                        except json.JSONDecodeError:
                            continue
                        choice = (chunk.get("choices") or [{}])[0]
                        delta = choice.get("delta") or {}
                        if delta.get("content"):
                            yield LLMDelta(type="content", content_delta=str(delta["content"]))
                        if delta.get("tool_calls"):
                            for tc in delta["tool_calls"]:
                                fn = tc.get("function") or {}
                                args_str = fn.get("arguments", "")
                                try:
                                    args = json.loads(args_str) if args_str else {}
                                except json.JSONDecodeError:
                                    args = {}
                                yield LLMDelta(
                                    type="tool_call",
                                    tool_call=ToolCallSpec(
                                        id=str(tc.get("id", "")),
                                        name=str(fn.get("name", "")),
                                        arguments=args,
                                    ),
                                )
                        if choice.get("finish_reason"):
                            fr = str(choice["finish_reason"])
                            if fr not in ("stop", "tool_calls", "length", "error"):
                                fr = "error"
                            yield LLMDelta(type="finish", finish_reason=fr)  # type: ignore[arg-type]
            except _RETRY_EXCEPTIONS as exc:
                raise LLMTransportError(f"GLM stream failed: {exc}") from exc

        return gen()
