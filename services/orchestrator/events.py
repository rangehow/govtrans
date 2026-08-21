"""Run event schema (§10). Events carry actions/evidence/summaries — never
model chain-of-thought. Persisted to run_events and streamed over SSE with
per-run monotonic seq for cursor resume.
"""
from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class RunEventOut(BaseModel):
    id: str
    run_id: str
    seq: int
    type: str
    phase: str
    status: str  # started | progress | completed | failed
    title: str
    summary: str | None = None
    progress: float | None = None
    segment_ids: list[str] = Field(default_factory=list)
    evidence: list[dict] = Field(default_factory=list)
    metrics: dict = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    def to_sse(self) -> str:
        return f"id: {self.seq}\ndata: {self.model_dump_json()}\n\n"
