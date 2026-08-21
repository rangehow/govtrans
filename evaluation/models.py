"""Evaluation persistence (§36/§37/§38). Every benchmark run pins the exact
configuration it measured — no release by vibes.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, String
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class BenchmarkRun(Base):
    __tablename__ = "benchmark_runs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(128))
    # "baseline" rows are the never-deleted single-pass LLM baseline (§38)
    kind: Mapped[str] = mapped_column(String(16), default="pipeline")  # pipeline|baseline
    gold_set: Mapped[str] = mapped_column(String(128))
    model: Mapped[str | None] = mapped_column(String(128), nullable=True)
    skill_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    pipeline_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    corpus_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running")  # running|completed|failed
    # {"chrf": ..., "numbers": ..., "terminology": ..., "mqm": {...},
    #  "latency_ms": ..., "tokens": {...}, "items": N}
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
