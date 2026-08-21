"""Style distillation models (§19). StyleRules are mined offline from the
parallel corpus, reviewed by humans, and only then versioned into skills.
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
    source_count: Mapped[int] = mapped_column(default=0)
    domains: Mapped[list] = mapped_column(JSON, default=list)
    confidence: Mapped[float] = mapped_column(Float, default=0.0)
    status: Mapped[str] = mapped_column(String(16), default="candidate")  # candidate|approved|rejected
    version: Mapped[str] = mapped_column(String(32), default="0.1.0")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
