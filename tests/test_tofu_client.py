import pytest

from agents.roles.llm import extract_json
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
        from agents.roles.llm import RoleError
        with pytest.raises(RoleError):
            extract_json("no json here at all")


class TestTofuErrorClassification:
    def test_429_retryable(self):
        err = _classify_http(429, "rate limited")
        assert err.kind == "ratelimit" and err.retryable

    def test_500_retryable(self):
        err = _classify_http(503, "down")
        assert err.kind == "server" and err.retryable

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
