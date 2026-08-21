"""TofuClient — GovTrans <-> ToFu agent runtime (HTTP/SSE).

ToFu runs as an independent service (AD-01; we never patch its core).
Verified public API surface (chatui repo, routes/api_v1/*.py):

  POST /api/v1/providers                  register_provider  -> {"id": "prov_..."}
  POST /api/v1/agent/run                  run_agent (Idempotency-Key supported)
  GET  /api/v1/tasks/{task_id}            get_task
  GET  /api/v1/tasks/{task_id}/stream     stream_task (SSE, ?cursor=N / Last-Event-ID)
  POST /api/v1/tasks/{task_id}/abort      abort_task

Guarantees implemented here: timeout, retry with backoff (429/5xx/network),
SSE reconnect with cursor resume, structured TofuError, idempotency keys.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

import httpx

logger = logging.getLogger("govtrans.tofu")


class TofuError(Exception):
    """Structured error. kind: timeout|ratelimit|network|server|invalid|aborted"""

    def __init__(self, kind: str, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retryable = retryable


@dataclass
class AgentResult:
    task_id: str
    status: str                       # done | error | aborted
    text: str = ""                    # final assistant message
    usage: dict[str, int] = field(default_factory=dict)  # input_tokens/output_tokens when reported
    raw: dict[str, Any] = field(default_factory=dict)


def _classify_http(status: int, body: str) -> TofuError:
    if status == 429:
        return TofuError("ratelimit", f"ToFu 429: {body[:200]}", status=status, retryable=True)
    if status >= 500:
        return TofuError("server", f"ToFu {status}: {body[:200]}", status=status, retryable=True)
    return TofuError("invalid", f"ToFu {status}: {body[:200]}", status=status, retryable=False)


class TofuClient:
    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 120.0,
        max_retries: int = 3,
        backoff_base: float = 1.5,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0))

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ http
    async def _request(self, method: str, path: str, *, retryable: bool, **kwargs) -> httpx.Response:
        attempt = 0
        while True:
            attempt += 1
            try:
                resp = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
            except httpx.TimeoutException as exc:
                err = TofuError("timeout", f"ToFu timeout after {self.timeout}s", retryable=True)
                if attempt > self.max_retries or not retryable:
                    raise err from exc
                await self._sleep(attempt)
                continue
            except httpx.TransportError as exc:
                err = TofuError("network", f"ToFu network error: {exc}", retryable=True)
                if attempt > self.max_retries or not retryable:
                    raise err from exc
                await self._sleep(attempt)
                continue
            if resp.status_code < 400:
                return resp
            err = _classify_http(resp.status_code, resp.text)
            if attempt > self.max_retries or not (retryable and err.retryable):
                raise err
            await self._sleep(attempt)

    async def _sleep(self, attempt: int) -> None:
        await asyncio.sleep(min(self.backoff_base**attempt, 15.0))

    # ------------------------------------------------------------------- API
    async def register_provider(
        self, name: str, base_url: str, *, api_key: str | None = None, models: list[str] | None = None
    ) -> str:
        payload: dict[str, Any] = {"name": name, "base_url": base_url}
        if api_key:
            payload["api_key"] = api_key
        if models:
            payload["models"] = models
        resp = await self._request("POST", "/api/v1/providers", retryable=True, json=payload)
        data = resp.json()
        provider_id = data.get("id") or data.get("provider_id") or ""
        logger.info("registered ToFu provider name=%s id=%s", name, provider_id)
        return provider_id

    async def run_agent(
        self,
        *,
        messages: list[dict[str, str]],
        model: str,
        provider: dict[str, str] | None = None,
        config: dict[str, Any] | None = None,
        timeout_s: int = 600,
        idempotency_key: str | None = None,
    ) -> AgentResult:
        """Non-streaming agent run. Idempotency-Key makes retries safe."""
        key = idempotency_key or uuid.uuid4().hex
        payload: dict[str, Any] = {
            "messages": messages,
            "model": model,
            "stream": False,
            "timeout_s": timeout_s,
        }
        if provider:
            payload["provider"] = provider
        if config:
            payload["config"] = config
        started = time.monotonic()
        resp = await self._request(
            "POST", "/api/v1/agent/run", retryable=True,
            json=payload, headers={"Idempotency-Key": key},
        )
        data = resp.json()
        return AgentResult(
            task_id=data.get("task_id", ""),
            status=data.get("status", "done"),
            text=_extract_text(data),
            usage=_extract_usage(data),
            raw=data,
        )

    async def get_task(self, task_id: str) -> dict[str, Any]:
        resp = await self._request("GET", f"/api/v1/tasks/{task_id}", retryable=True)
        return resp.json()

    async def abort_task(self, task_id: str) -> dict[str, Any]:
        resp = await self._request("POST", f"/api/v1/tasks/{task_id}/abort", retryable=False)
        return resp.json()

    async def stream_task(self, task_id: str, *, cursor: int = 0) -> AsyncIterator[dict[str, Any]]:
        """Yield SSE events for a task, resuming from cursor on disconnect.

        Each yielded dict has at least: seq (int, from SSE id) and data (dict).
        Terminal events have data.type in {done, error, aborted}.
        """
        last_id = cursor
        while True:
            headers = {"Last-Event-ID": str(last_id)} if last_id else {}
            params = {"cursor": last_id} if last_id else {}
            try:
                async with self._client.stream(
                    "GET", f"{self.base_url}/api/v1/tasks/{task_id}/stream",
                    headers=headers, params=params,
                ) as resp:
                    if resp.status_code >= 400:
                        raise _classify_http(resp.status_code, (await resp.aread()).decode())
                    async for event in _parse_sse(resp):
                        last_id = max(last_id, event["seq"])
                        yield event
                        if event["data"].get("type") in ("done", "error", "aborted"):
                            return
                    return  # clean EOF after terminal event
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                logger.warning("SSE disconnected task=%s cursor=%d: %s — resuming", task_id, last_id, exc)
                await asyncio.sleep(1.0)
                continue


# ------------------------------------------------------------ helpers
def _extract_text(data: dict[str, Any]) -> str:
    for key in ("text", "output", "result", "content"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val
    messages = data.get("messages") or []
    for msg in reversed(messages):
        if msg.get("role") == "assistant" and isinstance(msg.get("content"), str):
            return msg["content"]
    return ""


def _extract_usage(data: dict[str, Any]) -> dict[str, int]:
    usage = data.get("usage") or {}
    return {
        "input_tokens": int(usage.get("input_tokens") or usage.get("prompt_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or usage.get("completion_tokens") or 0),
    }


async def _parse_sse(resp: httpx.Response) -> AsyncIterator[dict[str, Any]]:
    event_id = 0
    data_lines: list[str] = []
    async for line in resp.aiter_lines():
        if line.startswith(":"):  # heartbeat comment
            continue
        if line.startswith("id:"):
            try:
                event_id = int(line[3:].strip())
            except ValueError:
                event_id += 1
        elif line.startswith("data:"):
            data_lines.append(line[5:].strip())
        elif line == "" and data_lines:
            payload = "\n".join(data_lines)
            data_lines = []
            try:
                data = json.loads(payload)
            except json.JSONDecodeError:
                data = {"type": "raw", "text": payload}
            yield {"seq": event_id, "data": data}
