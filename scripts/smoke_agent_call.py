#!/usr/bin/env python3
"""Smoke: one real agent call through ToFu -> DashScope (Task 9).

Requires DASHSCOPE_API_KEY in the environment (or .env). Exits non-zero with
an actionable message when the key is missing or the runtime is unreachable.
Prints NO secrets.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from apps.api.config import get_settings
from services.orchestrator.tofu_client import TofuClient, TofuError


async def main() -> int:
    settings = get_settings()
    if not settings.dashscope_api_key or not settings.dashscope_api_key.get_secret_value():
        print("FAIL: DASHSCOPE_API_KEY is not set. Copy .env.example to .env and fill it.")
        return 1
    tofu = TofuClient(
        settings.tofu_base_url,
        api_key=(
            settings.tofu_api_key.get_secret_value()
            if settings.tofu_api_key
            else None
        ),
        timeout=settings.tofu_timeout_seconds,
        max_retries=settings.tofu_max_retries,
        max_concurrency=settings.tofu_max_concurrency,
    )
    try:
        result = await tofu.run_agent(
            messages=[{"role": "user", "content": "Reply with the single word: OK"}],
            model=settings.fast_model,
            provider={
                "base_url": settings.dashscope_base_url,
                "api_key": settings.dashscope_api_key.get_secret_value(),
            },
            config={"temperature": 0},
            timeout_s=60,
            idempotency_key="smoke-agent-call-v1",
        )
    except TofuError as exc:
        print(f"FAIL: ToFu call failed kind={exc.kind} retryable={exc.retryable}: {exc}")
        return 2
    finally:
        await tofu.aclose()
    print(f"OK: task_id={result.task_id} status={result.status} "
          f"reply={result.text[:80]!r} usage={result.usage}")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
