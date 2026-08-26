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

    def __init__(
        self,
        kind: str,
        message: str,
        *,
        status: int | None = None,
        retryable: bool = False,
        retry_after: float | None = None,
    ):
        super().__init__(message)
        self.kind = kind
        self.status = status
        self.retryable = retryable
        self.retry_after = retry_after


@dataclass
class AgentResult:
    task_id: str
    status: str                       # done | error | aborted
    text: str = ""                    # final assistant message
    usage: dict[str, int] = field(default_factory=dict)  # input_tokens/output_tokens when reported
    raw: dict[str, Any] = field(default_factory=dict)


def _classify_http(status: int, body: str) -> TofuError:
    payload: dict[str, Any] = {}
    try:
        parsed = json.loads(body)
        if isinstance(parsed, dict):
            payload = parsed
    except (json.JSONDecodeError, TypeError):
        pass
    raw_retry_after = payload.get("retry_after")
    try:
        retry_after = max(0.0, float(raw_retry_after)) if raw_retry_after is not None else None
    except (TypeError, ValueError):
        retry_after = None
    if status == 429:
        return TofuError(
            "ratelimit",
            f"ToFu 429: {body[:200]}",
            status=status,
            retryable=True,
            retry_after=retry_after,
        )
    if status >= 500:
        kind = "overloaded" if payload.get("error_kind") == "overloaded" else "server"
        return TofuError(
            kind,
            f"ToFu {status}: {body[:200]}",
            status=status,
            retryable=True,
            retry_after=retry_after,
        )
    return TofuError("invalid", f"ToFu {status}: {body[:200]}", status=status, retryable=False)


class TofuClient:
    def __init__(
        self,
        base_url: str,
        *,
        api_key: str | None = None,
        timeout: float = 120.0,
        max_retries: int = 6,
        backoff_base: float = 1.5,
        max_concurrency: int = 12,
        admission_timeout: float = 180.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries
        self.backoff_base = backoff_base
        self.admission_timeout = max(0.0, admission_timeout)
        self._agent_slots = asyncio.Semaphore(max(1, max_concurrency))
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(timeout, connect=10.0),
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    # ------------------------------------------------------------------ http
    async def _request(self, method: str, path: str, *, retryable: bool, **kwargs) -> httpx.Response:
        attempt = 0
        transient_failures = 0
        admission_attempts = 0
        admission_started_at: float | None = None
        request_headers = dict(kwargs.get("headers") or {})
        idempotency_base = request_headers.get("Idempotency-Key")
        while True:
            attempt += 1
            try:
                resp = await self._client.request(method, f"{self.base_url}{path}", **kwargs)
            except httpx.TimeoutException as exc:
                err = TofuError("timeout", f"ToFu timeout after {self.timeout}s", retryable=True)
                transient_failures += 1
                if transient_failures > self.max_retries or not retryable:
                    raise err from exc
                await self._sleep(transient_failures)
                continue
            except httpx.TransportError as exc:
                err = TofuError("network", f"ToFu network error: {exc}", retryable=True)
                transient_failures += 1
                if transient_failures > self.max_retries or not retryable:
                    raise err from exc
                await self._sleep(transient_failures)
                continue
            if resp.status_code < 400:
                return resp
            err = _classify_http(resp.status_code, resp.text)
            if not (retryable and err.retryable):
                raise err
            if err.kind == "overloaded":
                # Admission pressure is a queueing condition, not a failed LLM
                # request. Give it its own bounded wait budget so a short load
                # spike cannot fail an otherwise healthy document merely by
                # consuming the network/server retry allowance.
                now = time.monotonic()
                if admission_started_at is None:
                    admission_started_at = now
                if now - admission_started_at >= self.admission_timeout:
                    raise err
                admission_attempts += 1
                if idempotency_base:
                    # ToFu can cache a pre-admission 503 under the idempotency
                    # key. The rejection proves no task was accepted, so a new
                    # admission suffix is safe. Ambiguous network failures keep
                    # the prior key to preserve at-most-once task creation.
                    request_headers["Idempotency-Key"] = (
                        f"{idempotency_base}:admission:{admission_attempts}"
                    )
                    kwargs["headers"] = request_headers
                await self._sleep(admission_attempts, retry_after=err.retry_after)
                continue

            transient_failures += 1
            if transient_failures > self.max_retries:
                raise err
            await self._sleep(transient_failures, retry_after=err.retry_after)

    async def _sleep(self, attempt: int, *, retry_after: float | None = None) -> None:
        backoff = min(self.backoff_base**attempt, 15.0)
        await asyncio.sleep(min(max(backoff, retry_after or 0.0), 30.0))

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
        """Start an asynchronous ToFu task, then await its bounded result.

        The old blocking HTTP request could outlive this client's read timeout.
        Retrying that ambiguous POST caused another in-flight model task every
        120 seconds on a busy runtime. A task handle makes acceptance explicit:
        after HTTP 202, all waiting is done with safe GETs against that exact
        task and a timeout aborts it before the caller falls back.
        """
        key = idempotency_key or uuid.uuid4().hex
        payload: dict[str, Any] = {
            "messages": messages,
            "model": model,
            "stream": False,
            "async": True,
            "timeout_s": timeout_s,
        }
        if provider:
            payload["provider"] = provider
        if config:
            payload["config"] = config
        # A single semaphore covers every run using this client. Per-stage
        # limits optimize one document; this guard prevents several documents
        # from collectively stampeding the shared ToFu admission controller.
        async with self._agent_slots:
            resp = await self._request(
                "POST", "/api/v1/agent/run", retryable=True,
                json=payload, headers={"Idempotency-Key": key},
            )
        data = resp.json()
        task_id = data.get("task_id", "")
        status = data.get("status", "done")
        # Compatibility with older/mock runtimes that still settle the request
        # synchronously and return the final content directly.
        if not task_id or status in {"done", "error", "aborted"}:
            if status == "error":
                raise self._task_error(data)
            if status == "aborted":
                raise TofuError("aborted", "ToFu task was aborted", retryable=False)
            return AgentResult(
                task_id=task_id,
                status=status,
                text=_extract_text(data),
                usage=_extract_usage(data),
                raw=data,
            )

        logger.info("ToFu task accepted id=%s model=%s", task_id, model)
        task = await self._wait_for_task(task_id, timeout_s=timeout_s)
        return AgentResult(
            task_id=task_id,
            status=task.get("status", "done"),
            text=_extract_text(task),
            usage=_extract_usage(task),
            raw=task,
        )

    @staticmethod
    def _task_error(task: dict[str, Any]) -> TofuError:
        error = task.get("error")
        if isinstance(error, dict):
            message = str(error.get("message") or error.get("detail") or error)
        else:
            message = str(error or "unknown task error")
        return TofuError("server", f"ToFu task failed: {message[:300]}", retryable=True)

    async def _wait_for_task(self, task_id: str, *, timeout_s: int) -> dict[str, Any]:
        """Poll one accepted task without ever resubmitting its model work."""
        try:
            async with asyncio.timeout(max(1, timeout_s)):
                while True:
                    task = await self.get_task(task_id)
                    status = task.get("status")
                    if status == "done":
                        return task
                    if status == "error":
                        raise self._task_error(task)
                    if status == "aborted":
                        raise TofuError(
                            "aborted", f"ToFu task {task_id} was aborted", retryable=False
                        )
                    await asyncio.sleep(1.0)
        except TimeoutError as exc:
            # Do not leave an accepted task consuming capacity before a direct
            # provider fallback starts the same logical request.
            try:
                await self.abort_task(task_id)
            except Exception as abort_exc:
                logger.warning("failed to abort timed-out ToFu task=%s: %s", task_id, abort_exc)
            raise TofuError(
                "timeout",
                f"ToFu task {task_id} did not finish within {timeout_s}s",
                retryable=True,
            ) from exc

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
