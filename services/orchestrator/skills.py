"""Translation style-skill registry.

The boundary is intentional and enforced in code:
- Skill = versioned writing/register/cohesion rules.
- Corpus = evidence used to distill rules; never injected wholesale.
- Terminology = explicit lexical contracts stored in the terminology DB or on
  a run. A Skill cannot create a mandatory term.
"""

from __future__ import annotations

import hashlib
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

GENERIC_BASE_SKILLS = ["gov-multilingual-core"]
ZH_EN_BASE_SKILLS = ["gov-cn-en-core", "gov-number-name-formatting"]
BASE_SKILLS = [*GENERIC_BASE_SKILLS, *ZH_EN_BASE_SKILLS]
STYLE_SKILLS = [
    "scio-white-paper-distilled",
    "gov-white-paper",
    "gov-policy-document",
    "gov-leader-speech",
    "gov-press-conference",
]
# Backwards-compatible name for callers that mean unconditional guardrails.
DEFAULT_SKILLS = GENERIC_BASE_SKILLS

DOCUMENT_STYLE_DEFAULTS = {
    "white_paper": ["scio-white-paper-distilled"],
    "policy_document": ["gov-policy-document"],
    "leader_speech": ["gov-leader-speech"],
    "press_conference": ["gov-press-conference"],
    "report": ["gov-policy-document"],
    "notice": ["gov-policy-document"],
}

SKILL_LABELS = {
    "gov-multilingual-core": "多语种政务翻译基础规则",
    "gov-cn-en-core": "政务英语基础规则",
    "gov-number-name-formatting": "数字与专名规则",
    "scio-white-paper-distilled": "国新办白皮书文风",
    "gov-white-paper": "通用白皮书文风",
    "gov-policy-document": "政策文件文风",
    "gov-leader-speech": "领导人讲话文风",
    "gov-press-conference": "新闻发布会文风",
}


def base_skills_for(source_language: str, target_language: str) -> list[str]:
    names = list(GENERIC_BASE_SKILLS)
    if source_language == "zh" and target_language == "en":
        names.extend(ZH_EN_BASE_SKILLS)
    return names


@lru_cache(maxsize=32)
def load_skill_rules(name: str) -> str:
    path = SKILLS_DIR / name / "SKILL.md"
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _frontmatter(name: str) -> dict[str, str]:
    text = load_skill_rules(name)
    if not text.startswith("---\n"):
        return {}
    _start, frontmatter, _rest = text.split("---", 2)
    result: dict[str, str] = {}
    for line in frontmatter.splitlines():
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def _approved_distilled_rules() -> list[Any]:
    """Read active atomic rules without making the filesystem Skill mutable."""
    from sqlalchemy import select

    from apps.api.db import SessionLocal
    from pipelines.style_distillation.models import StyleRule

    with SessionLocal() as session:
        return list(
            session.execute(
                select(StyleRule)
                .where(StyleRule.status == "approved")
                .order_by(StyleRule.confidence.desc(), StyleRule.source_count.desc())
                .limit(100)
            ).scalars()
        )


def _render_distilled_rules() -> str:
    rules = _approved_distilled_rules()
    if not rules:
        return ""
    lines = ["## 从官方对齐语料自动生效的增量规则"]
    for rule in rules:
        lines.append(
            f"- {rule.rule}（官方文档 {rule.source_count} 份，置信度 {rule.confidence:.2f}）"
        )
    return "\n".join(lines)


def _base_rule_count(name: str) -> int:
    """Count visible atomic rules in the checked-in Skill contract."""
    return sum(
        bool(re.match(r"^\s*(?:\d+\.|-)\s+", line)) for line in load_skill_rules(name).splitlines()
    )


def skill_version(name: str) -> str:
    material = load_skill_rules(name)
    if name == "scio-white-paper-distilled":
        material += "\n" + _render_distilled_rules()
    return hashlib.sha256(material.encode()).hexdigest()[:12]


def resolve_style_skills(
    requested: list[str] | None,
    document_type: str | None,
    *,
    source_language: str = "zh",
    target_language: str = "en",
) -> list[str]:
    """Validate an explicit selection or choose the document-type default."""
    pair_supports_specialized_style = source_language == "zh" and target_language == "en"
    if requested is None:
        selected = (
            DOCUMENT_STYLE_DEFAULTS.get(document_type or "", [])
            if pair_supports_specialized_style
            else []
        )
    else:
        selected = requested
    unknown = sorted(set(selected) - set(STYLE_SKILLS))
    if unknown:
        raise ValueError(f"未知文风 Skill: {', '.join(unknown)}")
    if selected and not pair_supports_specialized_style:
        raise ValueError("当前专项文风 Skill 仅有 zh-en 版本；该语言对将使用多语种政务基础规则")
    return list(dict.fromkeys(selected))


def render_style_contract(
    selected: list[str],
    *,
    source_language: str = "zh",
    target_language: str = "en",
) -> str:
    """Compile guardrails and selected styles; never include terminology."""
    names = [
        *base_skills_for(source_language, target_language),
        *resolve_style_skills(
            selected,
            None,
            source_language=source_language,
            target_language=target_language,
        ),
    ]
    sections: list[str] = []
    for name in names:
        rules = load_skill_rules(name)
        if name == "scio-white-paper-distilled":
            extra = _render_distilled_rules()
            if extra:
                rules = f"{rules}\n\n{extra}"
        sections.append(f"### {SKILL_LABELS.get(name, name)} [{name}]\n{rules}")
    return "\n\n".join(sections)


def load_skill_terms(_name: str) -> dict[str, dict[str, Any]]:
    """Compatibility shim: Skills are no longer allowed to own terminology."""
    return {}


def skill_catalog() -> list[dict[str, Any]]:
    from sqlalchemy import func, select

    from apps.api.db import SessionLocal
    from pipelines.style_distillation.models import StyleRule

    with SessionLocal() as session:
        rule_counts = dict(
            session.execute(
                select(StyleRule.status, func.count(StyleRule.id)).group_by(StyleRule.status)
            ).all()
        )
    approved_count = int(rule_counts.get("approved", 0))
    candidate_count = int(rule_counts.get("candidate", 0))
    items: list[dict[str, Any]] = []
    for name in [*BASE_SKILLS, *STYLE_SKILLS]:
        meta = _frontmatter(name)
        default_for = [
            doc_type for doc_type, defaults in DOCUMENT_STYLE_DEFAULTS.items() if name in defaults
        ]
        items.append(
            {
                "id": name,
                "name": SKILL_LABELS.get(name, name),
                "description": meta.get("description", ""),
                "version": skill_version(name),
                "category": "foundation" if name in BASE_SKILLS else "style",
                "locked": name in BASE_SKILLS,
                "supported_pairs": ["*"] if name in GENERIC_BASE_SKILLS else ["zh-en"],
                "default_for": default_for,
                "source": meta.get("source"),
                "base_rule_count": _base_rule_count(name),
                "candidate_rule_count": (
                    candidate_count if name == "scio-white-paper-distilled" else 0
                ),
                "distilled_rule_count": (
                    approved_count if name == "scio-white-paper-distilled" else 0
                ),
            }
        )
    return items
