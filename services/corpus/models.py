"""Corpus persistence (§11). Raw provenance is always preserved: raw_html
and the parsed structure are both stored; alignment rows link back to the
exact documents they came from.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, Index, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db import Base

CURRENT_ALIGNMENT_VERSION = "2"


def _uuid() -> str:
    return uuid.uuid4().hex


class CorpusDocument(Base):
    __tablename__ = "corpus_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lang: Mapped[str] = mapped_column(String(16))  # BCP-47-compatible registry code
    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True)
    domain: Mapped[str | None] = mapped_column(String(64), nullable=True)
    raw_html: Mapped[str | None] = mapped_column(Text, nullable=True)   # provenance: as fetched
    raw_text: Mapped[str] = mapped_column(Text)                          # extracted plain text
    # parsed structure: [{"kind": "heading|paragraph|list_item|table_cell", "text": ...}]
    structure: Mapped[list] = mapped_column(JSON, default=list)
    doc_metadata: Mapped[dict] = mapped_column(JSON, default=dict)       # publish_date, source, ...
    content_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    fetched_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class DocumentPair(Base):
    __tablename__ = "document_pairs"
    __table_args__ = (
        Index("uq_document_pairs_zh_en", "zh_doc_id", "en_doc_id", unique=True),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    zh_doc_id: Mapped[str] = mapped_column(ForeignKey("corpus_documents.id"))
    en_doc_id: Mapped[str] = mapped_column(ForeignKey("corpus_documents.id"))
    match_method: Mapped[str] = mapped_column(String(32), default="manual")  # manual|url_heuristic|cli
    match_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="auto")  # auto|reviewed
    alignment_version: Mapped[str] = mapped_column(
        String(16), default=CURRENT_ALIGNMENT_VERSION
    )
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


class AlignedPair(Base):
    __tablename__ = "aligned_pairs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    pair_id: Mapped[str] = mapped_column(ForeignKey("document_pairs.id"), index=True)
    level: Mapped[str] = mapped_column(String(16))  # paragraph | sentence
    idx: Mapped[int] = mapped_column(String(32))    # position key e.g. "3.1" (para.sent)
    zh_text: Mapped[str] = mapped_column(Text)
    en_text: Mapped[str] = mapped_column(Text)
    score: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="auto")  # auto|approved|rejected
    # promoted TM entry id, set when this pair enters translation_memory
    tm_entry_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)


class CorpusSyncJob(Base):
    """Persisted long-running corpus synchronization state.

    A decade import can take several minutes. Keeping progress and results in
    the database lets the UI reconnect after refresh and lets API startup
    resume an interrupted job instead of asking a person to start over.
    """

    __tablename__ = "corpus_sync_jobs"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(String(32), default="scio", index=True)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    stage: Mapped[str] = mapped_column(String(32), default="catalog")
    since_year: Mapped[int] = mapped_column(Integer)
    through_year: Mapped[int] = mapped_column(Integer)
    domain: Mapped[str | None] = mapped_column(String(100), nullable=True)
    discovered: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)
    succeeded: Mapped[int] = mapped_column(Integer, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, default=0)
    sentence_pairs: Mapped[int] = mapped_column(Integer, default=0)
    current_title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    result: Mapped[dict] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
