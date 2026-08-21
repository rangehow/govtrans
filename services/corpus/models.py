"""Corpus persistence (§11). Raw provenance is always preserved: raw_html
and the parsed structure are both stored; alignment rows link back to the
exact documents they came from.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class CorpusDocument(Base):
    __tablename__ = "corpus_documents"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True, index=True)
    title: Mapped[str | None] = mapped_column(String(512), nullable=True)
    lang: Mapped[str] = mapped_column(String(8))  # zh | en
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

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    zh_doc_id: Mapped[str] = mapped_column(ForeignKey("corpus_documents.id"))
    en_doc_id: Mapped[str] = mapped_column(ForeignKey("corpus_documents.id"))
    match_method: Mapped[str] = mapped_column(String(32), default="manual")  # manual|url_heuristic|cli
    match_confidence: Mapped[float] = mapped_column(Float, default=1.0)
    status: Mapped[str] = mapped_column(String(16), default="auto")  # auto|reviewed
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
