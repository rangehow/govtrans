"""GovTrans API settings — every value comes from the environment.

No model name, provider URL, or key is hard-coded here beyond documented
defaults from .env.example. Secrets never appear in reprs or logs.
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field, SecretStr
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
    translator_model: str = Field(default="qwen-plus")
    review_model: str = Field(default="qwen-max")
    fast_model: str = Field(default="qwen-turbo")
    embedding_model: str = Field(default="text-embedding-v3")
    rerank_model: str = Field(default="gte-rerank-v2")

    # ToFu runtime
    tofu_base_url: str = Field(default="http://localhost:15000")
    tofu_timeout_seconds: float = Field(default=120.0)

    # Infra
    database_url: str = Field(default="sqlite:///./data/govtrans.db")
    redis_url: str = Field(default="redis://localhost:6379/0")
    s3_endpoint: str = Field(default="http://localhost:9000")
    s3_access_key: str = Field(default="govtrans")
    s3_secret_key: SecretStr | None = Field(default=None)
    s3_bucket: str = Field(default="govtrans-corpus")

    # Pipeline
    pipeline_version: str = Field(default="0.1.0")
    max_finalize_loops: int = Field(default=2)

    @property
    def is_production(self) -> bool:
        return self.govtrans_env == "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()
