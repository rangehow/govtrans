"""Terminology knowledge base models (§13).

terms / term_variants / term_evidence are separate tables on purpose:
evidence is what makes a term official, and variants have their own status
lifecycle (preferred -> alternative -> deprecated).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class Term(Base):
    __tablename__ = "terms"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    source_term: Mapped[str] = mapped_column(String(256), index=True)
    preferred_target: Mapped[str] = mapped_column(String(512))
    domain: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    context: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="preferred")  # preferred|deprecated
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    updated_at: Mapped[datetime] = mapped_column(
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


class TermVariant(Base):
    __tablename__ = "term_variants"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    term_id: Mapped[str] = mapped_column(ForeignKey("terms.id"), index=True)
    target: Mapped[str] = mapped_column(String(512))
    status: Mapped[str] = mapped_column(String(16), default="alternative")  # alternative|deprecated
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class TermEvidence(Base):
    __tablename__ = "term_evidence"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    term_id: Mapped[str] = mapped_column(ForeignKey("terms.id"), index=True)
    source_document: Mapped[str | None] = mapped_column(String(512), nullable=True)
    url: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    authority: Mapped[str] = mapped_column(String(32), default="official")  # official|trusted|general
    source_sentence: Mapped[str | None] = mapped_column(Text, nullable=True)
    target_sentence: Mapped[str | None] = mapped_column(Text, nullable=True)


class TermAuditLog(Base):
    """Every terminology mutation keeps an audit record (§35)."""

    __tablename__ = "term_audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    term_id: Mapped[str] = mapped_column(String(32), index=True)
    action: Mapped[str] = mapped_column(String(16))  # create|update|deprecate
    before: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    actor: Mapped[str] = mapped_column(String(64), default="system")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
