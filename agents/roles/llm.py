"""Shared LLM role invocation helper.

Every agent role (translator, reviewers, analyst, finalizer) goes through
call_role(): load prompt file -> render -> TofuClient.run_agent -> strict
JSON parse against agents/schemas/*.json -> ModelUsage accounting.

Invalid JSON from the model is retried with a repair instruction (§41:
'invalid JSON' is a tested failure mode, not a silent pass).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any

import httpx

from apps.api.config import Settings
from apps.api.db import SessionLocal
from services.languages import prompt_language_variables
from services.orchestrator.models import ModelUsage
from services.orchestrator.tofu_client import AgentResult, TofuClient, TofuError

logger = logging.getLogger("govtrans.roles")

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"
SCHEMAS_DIR = Path(__file__).resolve().parents[1] / "schemas"

JSON_REPAIR_SUFFIX = (
    "\n\nYour previous reply did not satisfy the required JSON contract. "
    "Correct the exact validation error below and reply with ONLY the JSON "
    "object, no markdown fences or commentary.\nValidation error: {error}\n"
    "Required schema: {schema}"
)


class RoleError(Exception):
    pass


async def _direct_provider_completion(
    *, settings: Settings, messages: list[dict[str, str]], model: str, temperature: float
) -> AgentResult:
    """Bounded fallback for a ToFu admission refusal.

    This is not used for arbitrary ToFu failures. It preserves the configured
    provider/model, requests JSON output, and still flows through schema
    validation and local usage accounting below.
    """
    if not settings.dashscope_api_key:
        raise TofuError("invalid", "direct provider fallback has no API key")
    url = f"{settings.dashscope_base_url.rstrip('/')}/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
        "response_format": {"type": "json_object"},
    }
    headers = {
        "Authorization": f"Bearer {settings.dashscope_api_key.get_secret_value()}",
        "Content-Type": "application/json",
    }
    timeout = httpx.Timeout(settings.tofu_timeout_seconds, connect=15.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        for attempt in range(3):
            try:
                response = await client.post(url, json=payload, headers=headers)
            except httpx.TimeoutException as exc:
                if attempt == 2:
                    raise TofuError(
                        "timeout", "direct model provider timed out", retryable=True
                    ) from exc
                await asyncio.sleep(1.5 ** (attempt + 1))
                continue
            except httpx.TransportError as exc:
                if attempt == 2:
                    raise TofuError(
                        "network", f"direct model provider network error: {exc}", retryable=True
                    ) from exc
                await asyncio.sleep(1.5 ** (attempt + 1))
                continue
            if response.status_code >= 400:
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    await asyncio.sleep(1.5 ** (attempt + 1))
                    continue
                kind = "ratelimit" if response.status_code == 429 else "server"
                raise TofuError(
                    kind,
                    f"direct model provider returned HTTP {response.status_code}",
                    status=response.status_code,
                    retryable=response.status_code >= 429,
                )
            try:
                data = response.json()
            except ValueError as exc:
                raise TofuError(
                    "invalid",
                    "direct model provider returned a non-JSON response",
                    status=response.status_code,
                ) from exc
            try:
                content = data["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise TofuError(
                    "invalid", "direct model provider returned an invalid response"
                ) from exc
            usage = data.get("usage") or {}
            return AgentResult(
                task_id=f"direct-{uuid.uuid4().hex}",
                status="done",
                text=content,
                usage={
                    "input_tokens": int(usage.get("prompt_tokens", 0) or 0),
                    "output_tokens": int(usage.get("completion_tokens", 0) or 0),
                },
                raw=data,
            )
    raise TofuError("server", "direct model provider failed", retryable=True)


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


def validate_schema(value: Any, schema: dict[str, Any], *, path: str = "$") -> None:
    """Validate the JSON-Schema subset used by agent contracts.

    Top-level required-field checks allowed malformed nested issue/change rows
    to reach the pipeline. Keeping this small validator local avoids making the
    model boundary depend on an optional runtime package.
    """
    expected = schema.get("type")
    type_checks = {
        "object": lambda item: isinstance(item, dict),
        "array": lambda item: isinstance(item, list),
        "string": lambda item: isinstance(item, str),
        "boolean": lambda item: isinstance(item, bool),
        "integer": lambda item: isinstance(item, int) and not isinstance(item, bool),
        "number": lambda item: isinstance(item, (int, float)) and not isinstance(item, bool),
    }
    if expected in type_checks and not type_checks[expected](value):
        raise RoleError(f"model JSON field {path} must be {expected}")
    if "enum" in schema and value not in schema["enum"]:
        raise RoleError(f"model JSON field {path} must be one of {schema['enum']}")
    if isinstance(value, str) and len(value) < int(schema.get("minLength", 0)):
        raise RoleError(f"model JSON field {path} is shorter than minLength")
    if isinstance(value, dict):
        missing = [name for name in schema.get("required", []) if name not in value]
        if missing:
            raise RoleError(f"model JSON field {path} missing required fields: {missing}")
        properties = schema.get("properties", {})
        for name, child in value.items():
            if name in properties:
                validate_schema(child, properties[name], path=f"{path}.{name}")
    if isinstance(value, list) and isinstance(schema.get("items"), dict):
        for index, child in enumerate(value):
            validate_schema(child, schema["items"], path=f"{path}[{index}]")


def validate_required_fields(payload: dict[str, Any], schema: dict[str, Any]) -> None:
    """Backward-compatible entry point retained for callers and tests."""
    validate_schema(payload, schema)


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
    # Keep legacy/evaluation callers safe while every production run passes an
    # explicit pair. No multilingual prompt should reach the model with raw
    # ``{{source_language}}`` placeholders.
    prompt = render(
        template,
        {**prompt_language_variables("zh", "en"), **variables},
    )
    request_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "base_url": settings.dashscope_base_url,
                "model": model,
                "prompt": prompt,
                "schema": schema_name,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()[:24]
    provider = {
        "base_url": settings.dashscope_base_url,
        "api_key": settings.dashscope_api_key.get_secret_value()
        if settings.dashscope_api_key
        else "",
    }

    result: AgentResult | None = None
    status = "ok"
    started = time.monotonic()
    last_error: Exception | None = None
    for attempt in range(max_json_retries + 1):
        repair = ""
        if attempt:
            repair = JSON_REPAIR_SUFFIX.format(
                error=str(last_error),
                schema=json.dumps(schema, ensure_ascii=False, separators=(",", ":")),
            )
        messages = [{"role": "user", "content": prompt + repair}]
        try:
            temperature = 0.0 if "reviewer" in role else 0.1
            try:
                result = await tofu.run_agent(
                    messages=messages,
                    model=model,
                    provider=provider,
                    config={"temperature": temperature},
                    # The prompt fingerprint is essential: a run can issue many
                    # calls with the same role/prompt template.
                    idempotency_key=(
                        f"govtrans:{run_id or 'adhoc'}:{role}:{request_fingerprint}:{attempt}"
                    ),
                    timeout_s=max(1, int(settings.tofu_timeout_seconds)),
                )
            except TofuError as exc:
                if not (
                    exc.kind in {"overloaded", "timeout"}
                    and settings.direct_llm_fallback_on_overload
                ):
                    raise
                logger.warning(
                    "role=%s ToFu unavailable (%s); using configured direct provider fallback",
                    role,
                    exc.kind,
                )
                result = await _direct_provider_completion(
                    settings=settings,
                    messages=messages,
                    model=model,
                    temperature=temperature,
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
