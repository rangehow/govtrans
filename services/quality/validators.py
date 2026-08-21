"""Deterministic QA validators (§22). Pure functions, fully unit-tested.

Each validator receives (source, translation) and returns findings:
{"category", "severity", "source_span", "target_span", "message", "suggested_fix"}
"""
from __future__ import annotations

import re
from typing import Any

Finding = dict[str, Any]

_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?%?|(?<![\d.])\.\d+%?")
_DATE_ZH_RE = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日?")
_DATE_EN_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")

_BRACKET_PAIRS = [("（", "）", "(", ")"), ("(", ")", "(", ")"), ("【", "】", "[", "]"), ("“", "”", '"', '"')]


def _norm_number(token: str) -> str:
    return token.replace(",", "").rstrip("%")


def validate_numbers(source: str, translation: str) -> list[Finding]:
    """Every number in the source must appear in the translation (multiset
    comparison, order-independent, comma/percent normalized)."""
    src = sorted(_norm_number(t) for t in _NUMBER_RE.findall(source))
    tgt = sorted(_norm_number(t) for t in _NUMBER_RE.findall(translation))
    findings: list[Finding] = []
    missing = list(src)
    for token in tgt:
        if token in missing:
            missing.remove(token)
    for token in sorted(set(missing)):
        findings.append({
            "category": "number",
            "severity": "critical",
            "source_span": token,
            "target_span": "",
            "message": f"源文数字 {token} 未在译文中出现",
            "suggested_fix": f"补译数字 {token}",
        })
    return findings


def validate_dates(source: str, translation: str) -> list[Finding]:
    findings: list[Finding] = []
    for year, month, day in _DATE_ZH_RE.findall(source):
        candidates = {
            f"{year}-{int(month):02d}-{int(day):02d}",
            f"{year}-{int(month)}-{int(day)}",
        }
        en_dates = {"-".join([y, str(int(m)), str(int(d))]) for y, m, d in _DATE_EN_RE.findall(translation)}
        en_dates |= {"-".join([y, m.zfill(2), d.zfill(2)]) for y, m, d in _DATE_EN_RE.findall(translation)}
        if not candidates & en_dates and f"{month}/{day}" not in translation and not re.search(
            rf"(?:January|February|March|April|May|June|July|August|September|October|November|December)"
            rf"\s+{int(day)},?\s+{year}", translation
        ):
            findings.append({
                "category": "date",
                "severity": "critical",
                "source_span": f"{year}年{month}月{day}日",
                "target_span": "",
                "message": f"源文日期 {year}年{month}月{day}日 未在译文中找到对应表达",
                "suggested_fix": f"补译日期 {year}-{int(month):02d}-{int(day):02d}",
            })
    return findings


def validate_brackets(source: str, translation: str) -> list[Finding]:
    """Unbalanced brackets in the translation are a major formatting error."""
    findings: list[Finding] = []
    for zh_open, zh_close, en_open, en_close in _BRACKET_PAIRS:
        if translation.count(en_open) != translation.count(en_close):
            findings.append({
                "category": "bracket",
                "severity": "major",
                "source_span": "",
                "target_span": f"{en_open}...{en_close}",
                "message": f"译文括号不配对：{en_open}={translation.count(en_open)} {en_close}={translation.count(en_close)}",
                "suggested_fix": "检查并补全括号",
            })
    return findings


def validate_quotes(source: str, translation: str) -> list[Finding]:
    findings: list[Finding] = []
    if translation.count('"') % 2 != 0:
        findings.append({
            "category": "quote",
            "severity": "major",
            "source_span": "",
            "target_span": '"',
            "message": "译文引号不配对",
            "suggested_fix": "检查引号开闭",
        })
    return findings


def validate_terminology(source: str, translation: str, glossary: list[dict]) -> list[Finding]:
    """Glossary conformance: when the source contains a mandatory term, the
    translation must contain the mandated rendering."""
    findings: list[Finding] = []
    for entry in glossary:
        src_term, target = entry.get("source", ""), entry.get("target", "")
        if src_term and src_term in source and target and target.lower() not in translation.lower():
            findings.append({
                "category": "terminology",
                "severity": "critical",
                "source_span": src_term,
                "target_span": target,
                "message": f"术语 {src_term} 未使用规定译法 “{target}”",
                "suggested_fix": f"改用规定译法 {target}",
            })
    return findings


# explicit Latin-letter boundaries: \b fails next to CJK (CJK counts as \w)
_ACRONYM_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Z0-9]{1,9}(?![A-Za-z0-9])")
_DOC_TITLE_RE = re.compile(r"《([^》]+)》")
_ENUM_ZH_RE = re.compile(r"、")


def validate_acronyms(source: str, translation: str) -> list[Finding]:
    """Latin acronyms in the source (GDP, CPC, BRI...) must survive."""
    findings: list[Finding] = []
    for token in sorted(set(_ACRONYM_RE.findall(source))):
        if token not in translation:
            findings.append({
                "category": "acronym",
                "severity": "major",
                "source_span": token,
                "target_span": "",
                "message": f"源文缩写 {token} 未在译文中保留",
                "suggested_fix": f"保留缩写 {token}",
            })
    return findings


def validate_entities(source: str, translation: str) -> list[Finding]:
    """《...》 document/work titles: count parity between source and target.
    Chinese book-title marks have no English equivalent — the translation
    must render them as quotes or italics, never drop them."""
    findings: list[Finding] = []
    titles = _DOC_TITLE_RE.findall(source)
    if not titles:
        return findings
    quoted = re.findall(r'"[^"]+"|\'[^\']+\'|“[^”]+”', translation)
    if len(quoted) < len(titles):
        for title in titles:
            findings.append({
                "category": "entity",
                "severity": "major",
                "source_span": f"《{title}》",
                "target_span": "",
                "message": f"文献名《{title}》可能在译文中缺失（引号/斜体计数不足）",
                "suggested_fix": f"确认《{title}》已以引号或斜体形式译出",
            })
    return findings


def validate_enumerations(source: str, translation: str) -> list[Finding]:
    """Chinese enumerations (A、B、C) must survive as parallel English lists.
    Heuristic: N 顿号 separators imply at least N parallel separators
    (commas / and / or) in the translation."""
    findings: list[Finding] = []
    zh_seps = len(_ENUM_ZH_RE.findall(source))
    if zh_seps < 1:
        return findings
    en_seps = translation.count(",") + len(re.findall(r"\band\b|\bor\b", translation))
    if en_seps < zh_seps:
        findings.append({
            "category": "enumeration",
            "severity": "major",
            "source_span": f"{zh_seps + 1} 项并列",
            "target_span": "",
            "message": f"源文 {zh_seps + 1} 项并列结构在译文中疑似丢失（并列连词/逗号不足）",
            "suggested_fix": "检查并列项是否全部译出且结构平行",
        })
    return findings


def run_deterministic(source: str, translation: str, glossary: list[dict] | None = None) -> list[Finding]:
    findings: list[Finding] = []
    findings += validate_numbers(source, translation)
    findings += validate_dates(source, translation)
    findings += validate_brackets(source, translation)
    findings += validate_quotes(source, translation)
    findings += validate_acronyms(source, translation)
    findings += validate_entities(source, translation)
    findings += validate_enumerations(source, translation)
    if glossary:
        findings += validate_terminology(source, translation, glossary)
    return findings
