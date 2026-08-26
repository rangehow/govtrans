"""Translation Memory model (§12).

Lexical search works on every dialect (SQL LIKE + Python-side scoring).
Semantic/vector search activates on PostgreSQL+pgvector via the embedding
column; on SQLite the field stays NULL and hybrid ranking degrades to
lexical + metadata — same code path, explicit capability flag.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db import Base

# Authority levels ordered by trust (§15).
AUTHORITY_LEVELS = [
    "official_verified",  # human-verified official parallel corpus
    "official_aligned",  # auto-aligned official corpus
    "government_term",  # government terminology base
    "official_web",  # official web results (allowlisted domains)
    "trusted",  # other trusted sources
    "general_web",  # everything else
]


def _uuid() -> str:
    return uuid.uuid4().hex


class TMEntry(Base):
    __tablename__ = "translation_memory"
    __table_args__ = (
        Index(
            "ix_translation_memory_language_pair",
            "source_language",
            "target_language",
        ),
    )

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    source: Mapped[str] = mapped_column(Text)
    target: Mapped[str] = mapped_column(Text)
    source_language: Mapped[str] = mapped_column(String(16), default="zh")
    target_language: Mapped[str] = mapped_column(String(16), default="en")
    document_type: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    domain: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    source_document: Mapped[str | None] = mapped_column(String(512), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    authority: Mapped[str] = mapped_column(String(32), default="official_aligned", index=True)
    # pgvector VECTOR(dim) on Postgres via migration; JSON list on SQLite.
    embedding: Mapped[list | None] = mapped_column(JSON, nullable=True)
    provenance: Mapped[dict] = mapped_column(JSON, default=dict)  # raw ingest lineage (§11)
    score_hint: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
