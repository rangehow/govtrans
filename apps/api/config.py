"""GovTrans API settings — every value comes from the environment.

No model name, provider URL, or key is hard-coded here beyond documented
defaults from .env.example. Secrets never appear in reprs or logs.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Runtime
    govtrans_env: str = Field(default="development")
    log_level: str = Field(default="INFO")
    govtrans_log_raw_content: bool = Field(default=False)
    # Escape hatch for UI-only local dev without an LLM key. Never set in prod.
    govtrans_allow_missing_keys: bool = Field(default=False)

    # LLM provider (OpenAI-compatible)
    dashscope_base_url: str = Field(
        default="https://dashscope.aliyuncs.com/compatible-mode/v1"
    )
    dashscope_api_key: SecretStr | None = Field(default=None)

    # Models per role
    # Pinned dated snapshots (reproducible; rolling aliases drift silently).
    # From the 2026-09 gov_whitepaper_v1 benchmark (40 official SCIO pairs):
    # qwen3.7-max-2026-06-08 led chrF (0.635) and terminology (0.942);
    # qwen3.7-flash-2026-07-15 is the fastest pinnable flash tier.
    translator_model: str = Field(default="qwen3.7-max-2026-06-08")
    review_model: str = Field(default="qwen3.7-max-2026-06-08")
    fast_model: str = Field(default="qwen3.7-flash-2026-07-15")
    # Per-role thinking policy (reasoning models only). None = provider
    # default (full thinking), 0 = thinking off, N>0 = thinking_budget=N.
    # Measured on gov_whitepaper_v1: translator budget 256 keeps 99% of
    # full-thinking quality at ~half the latency; fast tier loses nothing
    # with thinking off (extraction tasks, 1.6s vs 29s full thinking).
    translator_thinking_budget: int | None = Field(default=256)
    review_thinking_budget: int | None = Field(default=None)
    fast_thinking_budget: int | None = Field(default=0)

    @field_validator(
        "translator_thinking_budget",
        "review_thinking_budget",
        "fast_thinking_budget",
        mode="before",
    )
    @classmethod
    def _blank_thinking_budget_means_provider_default(cls, value: object) -> object:
        # An empty or "none" env value means "leave the provider default"
        # (full thinking), distinct from 0 which disables thinking.
        if isinstance(value, str) and value.strip().lower() in {"", "none"}:
            return None
        return value
    embedding_model: str = Field(default="text-embedding-v3")
    rerank_model: str = Field(default="gte-rerank-v2")

    # ToFu runtime
    tofu_base_url: str = Field(default="http://localhost:15001")
    # Optional for a trusted loopback development runtime; required by the
    # bundled production sidecar. Kept separate from the upstream model key.
    tofu_api_key: SecretStr | None = Field(default=None)
    # Reasoning models think 10-20s even on short segments; a full 6k-char
    # review batch can legitimately exceed 120s. An abort here restarts the
    # whole batch, so a too-tight timeout is a speed bug, not a safeguard.
    tofu_timeout_seconds: float = Field(default=300.0)
    tofu_max_retries: int = Field(default=6, ge=0, le=20)
    tofu_max_concurrency: int = Field(default=12, ge=1, le=64)
    tofu_admission_timeout_seconds: float = Field(default=30.0, ge=0.0, le=1_800.0)
    tofu_resource_retry_seconds: float = Field(default=15.0, ge=1.0, le=300.0)
    # Prevent a run from looking alive forever when shared inference capacity
    # is unavailable. The count is persisted per stage across API restarts.
    tofu_resource_max_waits: int = Field(default=3, ge=1, le=20)
    # Long model calls do not naturally produce business events. Emit an
    # honest, unchanged-progress liveness event often enough that the UI can
    # distinguish "still awaiting the model" from a frozen pipeline.
    run_progress_heartbeat_seconds: float = Field(default=8.0, ge=2.0, le=60.0)
    # Agent-runtime overload must not make ordinary structured translation
    # unusable. The fallback uses the exact same configured provider/model and
    # can be disabled for deployments that require ToFu-only egress.
    direct_llm_fallback_on_overload: bool = Field(default=True)

    # Infra
    database_url: str = Field(default="sqlite:///./data/govtrans.db")
    redis_url: str = Field(default="redis://localhost:6379/0")
    s3_endpoint: str = Field(default="http://localhost:9000")
    s3_access_key: str = Field(default="govtrans")
    s3_secret_key: SecretStr | None = Field(default=None)
    s3_bucket: str = Field(default="govtrans-corpus")

    # Pipeline
    pipeline_version: str = Field(default="0.1.0")
    max_finalize_loops: int = Field(default=3, ge=1, le=8)
    # Paragraphs are persistence/alignment units, not isolated model calls.
    # Only unusually long paragraphs are split; contiguous units are then
    # translated together in document-aware batches.
    segment_max_chars: int = Field(default=4_000, ge=500, le=12_000)
    translation_batch_max_chars: int = Field(default=12_000, ge=2_000, le=40_000)
    # Short documents can begin their normal translator call while analysis
    # and terminology research run. Set to 0 to force the fully serial path.
    early_translation_max_chars: int = Field(default=2_500, ge=0, le=20_000)
    translation_batch_concurrency: int = Field(default=3, ge=1, le=8)
    document_context_max_chars: int = Field(default=40_000, ge=4_000, le=100_000)
    translation_concurrency: int = Field(default=8, ge=1, le=32)
    review_concurrency: int = Field(default=8, ge=1, le=32)
    research_concurrency: int = Field(default=4, ge=1, le=16)
    max_term_candidates: int = Field(default=24, ge=1, le=100)
    max_official_search_terms: int = Field(default=4, ge=0, le=24)

    @property
    def is_production(self) -> bool:
        return self.govtrans_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
