"""Unit tests for the per-role thinking-policy plumbing in agents.roles.llm."""
from __future__ import annotations

import asyncio

import pytest

from agents.roles import llm
from agents.roles.llm import _thinking_params
from apps.api.config import Settings
from services.orchestrator.tofu_client import AgentResult

pytestmark = pytest.mark.unit


def test_thinking_params_none_keeps_provider_default():
    assert _thinking_params(None) == {}


def test_thinking_params_zero_disables_thinking():
    assert _thinking_params(0) == {"enable_thinking": False}


def test_thinking_params_positive_sets_budget():
    assert _thinking_params(256) == {"thinking_budget": 256}


class _CapturingTofu:
    def __init__(self) -> None:
        self.config: dict | None = None

    async def run_agent(self, *, config, **_kwargs):
        self.config = config
        return AgentResult(
            task_id="t1",
            status="done",
            text='{"translation": "ok"}',
            usage={},
            raw={},
        )


def test_call_role_forwards_thinking_params_to_tofu_config():
    tofu = _CapturingTofu()
    settings = Settings()
    asyncio.run(
        llm.call_role(
            tofu=tofu,
            settings=settings,
            role="translator",
            prompt_name="baseline_translate",
            variables={"source_text": "测试"},
            schema_name="baseline",
            model="qwen3.7-max-2026-06-08",
            thinking_budget=256,
        )
    )
    assert tofu.config == {"temperature": 0.1, "thinking_budget": 256}


def test_call_role_omits_thinking_params_when_budget_is_none():
    tofu = _CapturingTofu()
    settings = Settings()
    asyncio.run(
        llm.call_role(
            tofu=tofu,
            settings=settings,
            role="translator",
            prompt_name="baseline_translate",
            variables={"source_text": "测试"},
            schema_name="baseline",
            model="qwen3.7-max-2026-06-08",
            thinking_budget=None,
        )
    )
    assert tofu.config == {"temperature": 0.1}
