"""Run-domain persistence models (TranslationRun and children).

All enums are stored as strings for dialect portability (SQLite dev,
PostgreSQL prod). JSON columns hold structured payloads whose schema is
defined by pydantic models in services/orchestrator/events.py and
agents/schemas/.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from apps.api.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RunStatus:
    CREATED = "CREATED"
    PARSING = "PARSING"
    ANALYZING = "ANALYZING"
    RESEARCHING = "RESEARCHING"
    TRANSLATING = "TRANSLATING"
    REVIEWING = "REVIEWING"
    FINALIZING = "FINALIZING"
    QA = "QA"
    WAITING_RESOURCES = "WAITING_RESOURCES"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    QUALITY_GATE_FAILED = "QUALITY_GATE_FAILED"
    CANCELLED = "CANCELLED"
    # Kept only so historic rows created by pre-0.2 releases remain readable.
    # New runs never enter a human-approval state.
    WAITING_HUMAN_REVIEW = "WAITING_HUMAN_REVIEW"

    ACTIVE = {
        CREATED,
        PARSING,
        ANALYZING,
        RESEARCHING,
        TRANSLATING,
        REVIEWING,
        FINALIZING,
        QA,
        WAITING_RESOURCES,
    }
    TERMINAL = {COMPLETED, FAILED, QUALITY_GATE_FAILED, CANCELLED, WAITING_HUMAN_REVIEW}


class Confidentiality:
    PUBLIC = "PUBLIC"
    INTERNAL = "INTERNAL"
    CONFIDENTIAL = "CONFIDENTIAL"


class TranslationRun(Base):
    __tablename__ = "translation_runs"
    __table_args__ = (
        Index("ix_translation_runs_language_pair", "source_language", "target_language"),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    status: Mapped[str] = mapped_column(String(32), default=RunStatus.CREATED, index=True)
    direction: Mapped[str] = mapped_column(String(35), default="zh-en")
    source_language: Mapped[str] = mapped_column(String(16), default="zh")
    target_language: Mapped[str] = mapped_column(String(16), default="en")
    confidentiality: Mapped[str] = mapped_column(String(16), default=Confidentiality.PUBLIC)
    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_text: Mapped[str] = mapped_column(Text)
    # Style, terminology and inference strategy are deliberately separate:
    # style_skills controls register; manual_terms controls lexical choices;
    # translation_mode controls document batching/cohesion.
    style_skills: Mapped[list] = mapped_column(JSON, default=list)
    manual_terms: Mapped[list] = mapped_column(JSON, default=list)
    translation_mode: Mapped[str] = mapped_column(String(16), default="coherent")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    current_stage: Mapped[str | None] = mapped_column(String(32), nullable=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    loop_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Version pins — any result must be reproducible (docs/EVALUATION.md).
    pipeline_version: Mapped[str] = mapped_column(String(32))
    version_pins: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
    updated_at: Mapped[datetime] = mapped_column(default=_utcnow, onupdate=_utcnow, index=True)

    segments: Mapped[list["Segment"]] = relationship(
        back_populates="run", cascade="all, delete-orphan", order_by="Segment.idx"
    )
    issues: Mapped[list["Issue"]] = relationship(back_populates="run", cascade="all, delete-orphan")


class Segment(Base):
    __tablename__ = "segments"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("translation_runs.id"), index=True)
    idx: Mapped[int] = mapped_column(Integer)
    source: Mapped[str] = mapped_column(Text)
    translation: Mapped[str | None] = mapped_column(Text, nullable=True)
    # pending | translated | reviewed | final
    status: Mapped[str] = mapped_column(String(16), default="pending")
    # Versioned text history: {"ai_draft": ..., "reviewed": ..., "final": ...}
    versions: Mapped[dict] = mapped_column(JSON, default=dict)

    run: Mapped[TranslationRun] = relationship(back_populates="segments")


class RunEvent(Base):
    """Persisted SSE event. seq is per-run monotonic for cursor resume."""

    __tablename__ = "run_events"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_run_events_run_seq"),)

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("translation_runs.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)  # per-run monotonic
    type: Mapped[str] = mapped_column(String(48))
    phase: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))  # started|progress|completed|failed
    title: Mapped[str] = mapped_column(String(200))
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    segment_ids: Mapped[list] = mapped_column(JSON, default=list)
    evidence: Mapped[list] = mapped_column(JSON, default=list)
    metrics: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class Issue(Base):
    """MQM-flavoured QA issue (docs/TRANSLATION_PIPELINE.md §24)."""

    __tablename__ = "issues"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("translation_runs.id"), index=True)
    segment_id: Mapped[str | None] = mapped_column(ForeignKey("segments.id"), nullable=True)
    reviewer: Mapped[str] = mapped_column(String(48))
    severity: Mapped[str] = mapped_column(String(16))  # critical|major|minor
    category: Mapped[str] = mapped_column(String(48))
    source_span: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_span: Mapped[str | None] = mapped_column(Text, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    suggested_fix: Mapped[str | None] = mapped_column(Text, nullable=True)
    evidence_refs: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(16), default="open")  # open|resolved|dismissed
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)

    run: Mapped[TranslationRun] = relationship(back_populates="issues")


class DocumentGlossary(Base):
    """Per-run shared terminology contract. Translators must not deviate
    without emitting a term_exception (recorded in entries[].exception)."""

    __tablename__ = "document_glossaries"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str] = mapped_column(ForeignKey("translation_runs.id"), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    # [{"source": ..., "target": ..., "origin": "term_db|llm_proposed|official_search",
    #   "evidence": [...], "exception": null|{"reason":..., "evidence":...}}]
    entries: Mapped[list] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)


class ModelUsage(Base):
    """Per-LLM-call accounting (§40, §45). Never stores the API key; raw
    prompt/source text is only stored when GOVTRANS_LOG_RAW_CONTENT=true."""

    __tablename__ = "model_usage"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    run_id: Mapped[str | None] = mapped_column(String(32), index=True, nullable=True)
    role: Mapped[str] = mapped_column(String(48))  # translator|semantic_reviewer|...
    model: Mapped[str] = mapped_column(String(128))
    tofu_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    input_tokens: Mapped[int] = mapped_column(Integer, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, default=0)
    retries: Mapped[int] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(16), default="ok")  # ok|error|timeout
    created_at: Mapped[datetime] = mapped_column(default=_utcnow)
