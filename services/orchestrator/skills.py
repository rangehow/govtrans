"""Translation Skill loader (§18). Skills are RULES (SKILL.md), not corpus,
not glossary. Versioned by content hash for run reproducibility (§44).
"""
from __future__ import annotations

import hashlib
from functools import lru_cache
from pathlib import Path

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

DEFAULT_SKILLS = ["gov-cn-en-core", "gov-number-name-formatting"]


@lru_cache(maxsize=32)
def load_skill_rules(name: str) -> str:
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def skill_version(name: str) -> str:
    return hashlib.sha256(load_skill_rules(name).encode()).hexdigest()[:12]
