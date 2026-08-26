import asyncio
import json

import httpx
import pytest

from agents.roles.llm import RoleError, call_role, extract_json, validate_schema
from apps.api.config import Settings
from services.orchestrator.tofu_client import AgentResult
from services.orchestrator.tofu_client import TofuError, _classify_http

pytestmark = pytest.mark.unit


class TestExtractJson:
    def test_plain(self):
        assert extract_json('{"a": 1}') == {"a": 1}

    def test_markdown_fence(self):
        assert extract_json('```json\n{"a": 1}\n```') == {"a": 1}

    def test_surrounded_by_prose(self):
        assert extract_json('Sure! Here: {"a": 1} hope that helps') == {"a": 1}

    def test_invalid_raises(self):
        with pytest.raises(RoleError):
            extract_json("no json here at all")


class TestModelContract:
    def test_nested_schema_is_validated(self):
        schema = {
            "type": "object",
            "required": ["issues"],
            "properties": {
                "issues": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["severity"],
                        "properties": {"severity": {"type": "string", "enum": ["critical"]}},
                    },
                }
            },
        }
        with pytest.raises(RoleError, match="severity"):
            validate_schema({"issues": [{"severity": "catastrophic"}]}, schema)

    def test_idempotency_key_is_scoped_to_rendered_prompt(self):
        class FakeTofu:
            def __init__(self):
                self.keys = []

            async def run_agent(self, **kwargs):
                self.keys.append(kwargs["idempotency_key"])
                return AgentResult(task_id="task", status="done", text='{"translation":"ok"}')

        tofu = FakeTofu()
        settings = Settings(dashscope_api_key="test-placeholder", _env_file=None)

        async def invoke(source_text):
            return await call_role(
                tofu=tofu,
                settings=settings,
                role="baseline",
                prompt_name="baseline_translate",
                variables={"source_text": source_text},
                schema_name="baseline",
                model="test-model",
                run_id="same-run",
            )

        asyncio.run(invoke("第一段"))
        asyncio.run(invoke("第二段"))
        assert len(set(tofu.keys)) == 2


class TestTofuErrorClassification:
    def test_429_retryable(self):
        err = _classify_http(429, "rate limited")
        assert err.kind == "ratelimit" and err.retryable

    def test_500_retryable(self):
        err = _classify_http(503, "down")
        assert err.kind == "server" and err.retryable

    def test_overload_preserves_retry_hint(self):
        err = _classify_http(
            503,
            '{"error":"busy","error_kind":"overloaded","retry_after":5}',
        )
        assert err.kind == "overloaded" and err.retry_after == 5

    def test_400_not_retryable(self):
        err = _classify_http(400, "bad request")
        assert err.kind == "invalid" and not err.retryable


class FakeSSEResponse:
    def __init__(self, lines):
        self._lines = lines

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class TestSseParsing:
    def test_events_and_heartbeats(self):
        from services.orchestrator.tofu_client import _parse_sse

        lines = [
            ": heartbeat",
            "id: 1",
            'data: {"type": "progress", "pct": 0.5}',
            "",
            "id: 2",
            'data: {"type": "done"}',
            "",
        ]

        async def collect():
            return [ev async for ev in _parse_sse(FakeSSEResponse(lines))]

        import asyncio
        events = asyncio.run(collect())
        assert [e["seq"] for e in events] == [1, 2]
        assert events[1]["data"]["type"] == "done"


class TestTofuErrorType:
    def test_str(self):
        assert "boom" in str(TofuError("network", "boom", retryable=True))


def test_request_honors_server_retry_hint():
    calls = 0
    keys = []

    def handler(request):
        nonlocal calls
        calls += 1
        keys.append(request.headers.get("Idempotency-Key"))
        if calls == 1:
            return httpx.Response(
                503,
                json={"error": "busy", "error_kind": "overloaded", "retry_after": 7},
            )
        return httpx.Response(200, json={"text": "ok"})

    async def scenario():
        from services.orchestrator.tofu_client import TofuClient

        # Admission retries are deliberately independent of the ordinary
        # transient-error retry count.
        client = TofuClient("http://tofu.test", max_retries=0)
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        delays = []

        async def record_sleep(attempt, *, retry_after=None):
            delays.append((attempt, retry_after))

        client._sleep = record_sleep
        response = await client._request(
            "GET",
            "/probe",
            retryable=True,
            headers={"Idempotency-Key": "stable"},
        )
        await client.aclose()
        return response, delays

    response, delays = asyncio.run(scenario())
    assert response.status_code == 200
    assert delays == [(1, 7)]
    assert keys == ["stable", "stable:admission:1"]


def test_run_agent_uses_async_handle_and_never_reposts_while_waiting():
    requests = []
    polls = 0

    def handler(request):
        nonlocal polls
        requests.append((request.method, request.url.path))
        if request.method == "POST":
            payload = json.loads(request.content)
            assert payload["async"] is True
            return httpx.Response(
                202,
                json={"task_id": "task-accepted", "status": "running"},
            )
        polls += 1
        if polls == 1:
            return httpx.Response(200, json={"id": "task-accepted", "status": "running"})
        return httpx.Response(
            200,
            json={
                "id": "task-accepted",
                "status": "done",
                "content": '{"translation":"ok"}',
                "usage": {"input_tokens": 3, "output_tokens": 2},
            },
        )

    async def scenario():
        from services.orchestrator.tofu_client import TofuClient

        client = TofuClient("http://tofu.test", max_retries=0)
        await client._client.aclose()
        client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
        original_sleep = asyncio.sleep

        async def no_wait(_seconds):
            await original_sleep(0)

        import services.orchestrator.tofu_client as tofu_module

        old_sleep = tofu_module.asyncio.sleep
        tofu_module.asyncio.sleep = no_wait
        try:
            result = await client.run_agent(
                messages=[{"role": "user", "content": "translate"}],
                model="test-model",
                timeout_s=10,
                idempotency_key="stable-task",
            )
        finally:
            tofu_module.asyncio.sleep = old_sleep
            await client.aclose()
        return result

    result = asyncio.run(scenario())
    assert result.task_id == "task-accepted"
    assert result.text == '{"translation":"ok"}'
    assert requests.count(("POST", "/api/v1/agent/run")) == 1
    assert requests.count(("GET", "/api/v1/tasks/task-accepted")) == 2


def test_client_sends_sidecar_bearer_token_on_submit_and_poll():
    authorizations = []

    def handler(request):
        authorizations.append(request.headers.get("Authorization"))
        if request.method == "POST":
            return httpx.Response(202, json={"task_id": "secured", "status": "running"})
        return httpx.Response(200, json={"id": "secured", "status": "done", "text": "ok"})

    async def scenario():
        from services.orchestrator.tofu_client import TofuClient

        client = TofuClient("http://tofu.test", api_key="sidecar-test-token", max_retries=0)
        await client._client.aclose()
        client._client = httpx.AsyncClient(
            transport=httpx.MockTransport(handler),
            headers={"Authorization": "Bearer sidecar-test-token"},
        )
        try:
            return await client.run_agent(
                messages=[{"role": "user", "content": "hello"}],
                model="test-model",
                timeout_s=10,
            )
        finally:
            await client.aclose()

    result = asyncio.run(scenario())
    assert result.text == "ok"
    assert authorizations == ["Bearer sidecar-test-token", "Bearer sidecar-test-token"]
