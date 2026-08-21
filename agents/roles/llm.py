"""Shared LLM role invocation helper.

Every agent role (translator, reviewers, analyst, finalizer) goes through
call_role(): load prompt file -> render -> TofuClient.run_agent -> strict
JSON parse against agents/schemas/*.json -> ModelUsage accounting.

Invalid JSON from the model is retried with a repair instruction (§41:
'invalid JSON' is a tested failure mode, not a silent pass).
"""
from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

from apps.api.config import Settings
from apps.api.db import SessionLocal
from services.orchestrator.models import ModelUsage
from services.orchestrator.tofu_client import AgentResult, TofuClient, TofuError

logger = logging.getLogger("govtrans.roles")

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"

JSON_REPAIR_SUFFIX = (
    "\n\nYour previous reply was not valid JSON. Reply with ONLY the JSON object, "
    "no markdown fences, no commentary."
)


class RoleError(Exception):
    pass


def load_prompt(name: str) -> str:
    path = PROMPTS_DIR / f"{name}.md"
    return path.read_text(encoding="utf-8")


def load_schema(name: str) -> dict[str, Any]:
    path = SCHEMAS_DIR / f"{name}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def prompt_version(name: str) -> str:
    """Cheap content version pin for reproducibility (§44)."""
    import hashlib

    return hashlib.sha256(load_prompt(name).encode()).hexdigest()[:12]


def render(template: str, variables: dict[str, Any]) -> str:
    out = template
    for key, value in variables.items():
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
        out = out.replace("{{" + key + "}}", text)
    return out


def extract_json(text: str) -> dict[str, Any]:
    """Tolerant JSON extraction: strips markdown fences, then locates the
    first {...} block. Raises RoleError when nothing parses."""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.strip("`")
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    try:
        parsed = json.loads(cleaned)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end > start:
        try:
            parsed = json.loads(cleaned[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    raise RoleError(f"model returned invalid JSON: {text[:160]!r}")


def validate_required_fields(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    missing = [f for f in schema.get("required", []) if f not in payload]
    if missing:
        raise RoleError(f"model JSON missing required fields: {missing}")


async def call_role(
    *,
    tofu: TofuClient,
    settings: Settings,
    role: str,
    prompt_name: str,
    variables: dict[str, Any],
    schema_name: str,
    model: str,
    run_id: str | None = None,
    max_json_retries: int = 2,
) -> dict[str, Any]:
    """Invoke an LLM role via ToFu and return validated structured JSON."""
    template = load_prompt(prompt_name)
    schema = load_schema(schema_name)
    prompt = render(template, variables)
    provider = {
        "base_url": settings.dashscope_base_url,
        "api_key": settings.dashscope_api_key.get_secret_value() if settings.dashscope_api_key else "",
    }

    result: AgentResult | None = None
    status = "ok"
    started = time.monotonic()
    last_error: Exception | None = None
    for attempt in range(max_json_retries + 1):
        messages = [{"role": "user", "content": prompt + (JSON_REPAIR_SUFFIX if attempt else "")}]
        try:
            result = await tofu.run_agent(
                messages=messages,
                model=model,
                provider=provider,
                config={"temperature": 0.2},
                idempotency_key=f"{run_id or 'adhoc'}:{role}:{prompt_name}:{attempt}",
            )
            payload = extract_json(result.text)
            validate_required_fields(payload, schema)
            _record_usage(run_id, role, model, result, started, attempt, "ok")
            return payload
        except RoleError as exc:
            last_error = exc
            status = "invalid_json"
            logger.warning("role=%s invalid JSON (attempt %d): %s", role, attempt + 1, exc)
        except TofuError as exc:
            last_error = exc
            status = exc.kind
            _record_usage(run_id, role, model, result, started, attempt, exc.kind)
            raise
    _record_usage(run_id, role, model, result, started, max_json_retries, status)
    raise RoleError(f"role {role} failed after {max_json_retries + 1} attempts: {last_error}")


def _record_usage(
    run_id: str | None,
    role: str,
    model: str,
    result: AgentResult | None,
    started: float,
    retries: int,
    status: str,
) -> None:
    latency_ms = int((time.monotonic() - started) * 1000)
    try:
        with SessionLocal() as session:
            session.add(
                ModelUsage(
                    run_id=run_id,
                    role=role,
                    model=model,
                    tofu_task_id=result.task_id if result else None,
                    latency_ms=latency_ms,
                    input_tokens=result.usage.get("input_tokens", 0) if result else 0,
                    output_tokens=result.usage.get("output_tokens", 0) if result else 0,
                    retries=retries,
                    status=status,
                )
            )
            session.commit()
    except Exception:  # usage accounting must never crash a run
        logger.exception("failed to record model usage for role=%s", role)
