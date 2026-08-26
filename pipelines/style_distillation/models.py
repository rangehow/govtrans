"""Style distillation models (§19).

High-confidence rules supported by multiple official document pairs become
active automatically. Optional governance can exclude or exceptionally
activate weaker observations; translation never waits for that workflow.
Skill = rules; Corpus = evidence; Glossary = terminology (AD-03).
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Float, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from apps.api.db import Base


def _uuid() -> str:
    return uuid.uuid4().hex


class StyleRule(Base):
    __tablename__ = "style_rules"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    rule: Mapped[str] = mapped_column(Text)              # human-readable rule statement
    zh_pattern: Mapped[str] = mapped_column(String(256)) # the mined zh cue (e.g. 以…为…)
    en_rendering: Mapped[str] = mapped_column(String(512))
    examples: Mapped[list] = mapped_column(JSON, default=list)        # [{zh, en, pair_id}]
    counterexamples: Mapped[list] = mapped_column(JSON, default=list) # pairs where it failed
    source_count: Mapped[int] = mapped_column(default=0)  # distinct document pairs
    domains: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="candidate")  # candidate|approved|rejected
    # ``approved`` is the legacy storage value for an active runtime rule.
    # The source makes automatic activation visibly distinct from a rare
    # human exception without introducing an approval queue.
    activation_source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    activated_at: Mapped[datetime | None] = mapped_column(nullable=True)
    version: Mapped[str] = mapped_column(String(32), default="0.1.0")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
