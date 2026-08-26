"""Deterministic QA validators (§22). Pure functions, fully unit-tested.

Each validator receives (source, translation) and returns findings:
{"category", "severity", "source_span", "target_span", "message", "suggested_fix"}
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

Finding = dict[str, Any]

_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?%?|(?<![\d.])\.\d+%?")
_DATE_ZH_RE = re.compile(
    r"(?:(?P<year>\d{4})\s*年\s*)?"
    r"(?P<month>\d{1,2})\s*月\s*(?P<day>\d{1,2})\s*日?"
)
_MONTH_ONLY_ZH_RE = re.compile(
    r"(?<!\d)(?P<month>\d{1,2})\s*月(?!\s*\d{1,2}\s*日?)"
)
_EN_MONTHS = (
    r"January|Jan\.?|February|Feb\.?|March|Mar\.?|April|Apr\.?|May|"
    r"June|Jun\.?|July|Jul\.?|August|Aug\.?|September|Sept?\.?|"
    r"October|Oct\.?|November|Nov\.?|December|Dec\.?"
)
_EN_MONTH_NUMBER = {
    "jan": 1,
    "january": 1,
    "feb": 2,
    "february": 2,
    "mar": 3,
    "march": 3,
    "apr": 4,
    "april": 4,
    "may": 5,
    "jun": 6,
    "june": 6,
    "jul": 7,
    "july": 7,
    "aug": 8,
    "august": 8,
    "sep": 9,
    "sept": 9,
    "september": 9,
    "oct": 10,
    "october": 10,
    "nov": 11,
    "november": 11,
    "dec": 12,
    "december": 12,
}

_ZH_EN_CURRENCIES: dict[str, tuple[str, ...]] = {
    "人民币": ("renminbi", "yuan", "rmb", "cny"),
    "美元": ("u.s. dollar", "us dollar", "united states dollar", "usd"),
    "日元": ("japanese yen", "yen", "jpy"),
    "欧元": ("euro", "eur"),
    "英镑": ("pound sterling", "british pound", "sterling", "gbp"),
}

_BRACKET_PAIRS = [
    ("（", "）", "(", ")"),
    ("(", ")", "(", ")"),
    ("【", "】", "[", "]"),
    ("“", "”", '"', '"'),
]


def _norm_number(token: str) -> str:
    return token.replace(",", "").rstrip("%")


def _number_tokens(text: str) -> list[str]:
    """Extract quantities while excluding digits embedded in model/entity IDs.

    Tokens such as ``K3`` and ``GPT-5.6`` are lexical identifiers, not
    quantities whose occurrence count must be preserved. They are covered by
    the acronym/entity validators instead. Treating their digits as quantities
    made repeated names produce false release blockers.
    """
    tokens: list[str] = []
    for match in _NUMBER_RE.finditer(text):
        start, end = match.span()
        left = text[:start]
        right = text[end:]
        ordinal_suffix = bool(re.match(r"(?:st|nd|rd|th)\b", right, flags=re.IGNORECASE))
        adjacent_ascii_letter = (
            start > 0 and text[start - 1].isascii() and text[start - 1].isalpha()
        ) or (
            end < len(text)
            and text[end].isascii()
            and text[end].isalpha()
            and not ordinal_suffix
        )
        latin_identifier_prefix = bool(
            re.search(r"[A-Za-z][A-Za-z0-9._/]*[-_.]$", left)
        )
        latin_identifier_suffix = bool(re.match(r"[-_.][A-Za-z]", right))
        if adjacent_ascii_letter or latin_identifier_prefix or latin_identifier_suffix:
            continue
        tokens.append(match.group(0))
    return tokens


def _consume(counter: Counter[str], tokens: list[str]) -> None:
    for token in tokens:
        normalized = _norm_number(token)
        if counter[normalized] > 0:
            counter[normalized] -= 1


def _english_date_match(
    translation: str, year: str | None, month: str, day: str
) -> re.Match[str] | None:
    """Return an English rendering of one Chinese date.

    Month names are semantic equivalents of their source digits. Returning
    the concrete match also lets the number validator consume only the digits
    belonging to that date, so a second unrelated ``8`` cannot hide a real
    omission.
    """
    month_number = int(month)
    day_number = int(day)
    year_number = int(year) if year else None
    suffix = r"(?:st|nd|rd|th)?"
    if 1 <= month_number <= 12:
        # Match the full month family rather than interpolating one spelling.
        year_tail = rf"\s*,?\s*{year_number}\b" if year_number else r"(?:\s*,?\s*\d{4})?"
        patterns = [
            rf"\b(?:{_EN_MONTHS})\s+0?{day_number}{suffix}{year_tail}",
            rf"\b0?{day_number}{suffix}\s+(?:{_EN_MONTHS}){year_tail}",
        ]
        for pattern in patterns:
            for match in re.finditer(pattern, translation, flags=re.IGNORECASE):
                month_token = re.search(rf"(?:{_EN_MONTHS})", match.group(0), flags=re.IGNORECASE)
                if not month_token:
                    continue
                normalized_month = month_token.group(0).rstrip(".").casefold()
                if _EN_MONTH_NUMBER.get(normalized_month) == month_number:
                    return match

    if year_number is not None:
        numeric_patterns = [
            rf"(?<!\d){year_number}\s*[-/.]\s*0?{month_number}\s*[-/.]\s*0?{day_number}(?!\d)",
            rf"(?<!\d)0?{month_number}\s*[/.-]\s*0?{day_number}\s*[/.-]\s*{year_number}(?!\d)",
        ]
    else:
        numeric_patterns = [
            rf"(?<!\d)0?{month_number}\s*[/.-]\s*0?{day_number}(?!\s*[/.-]\s*\d|\d)",
        ]
    for pattern in numeric_patterns:
        match = re.search(pattern, translation)
        if match:
            return match
    return None


def _english_month_match(translation: str, month: str) -> re.Match[str] | None:
    month_number = int(month)
    for match in re.finditer(
        rf"(?<![A-Za-z])(?:{_EN_MONTHS})(?![A-Za-z])",
        translation,
        flags=re.IGNORECASE,
    ):
        normalized = match.group(0).rstrip(".").casefold()
        if _EN_MONTH_NUMBER.get(normalized) == month_number:
            return match
    return None


def _matched_date_numbers(source: str, translation: str) -> tuple[list[str], list[str]]:
    """Numbers to consume because a date/month pair is semantically equal.

    This includes month-only expressions such as ``7月中旬`` -> ``mid-July``.
    Source events and target matches are consumed in order so one translated
    month cannot conceal a second omitted occurrence.
    """
    source_tokens: list[str] = []
    target_tokens: list[str] = []
    target_cursor = 0
    source_events = [
        (match.start(), "date", match) for match in _DATE_ZH_RE.finditer(source)
    ] + [
        (match.start(), "month", match) for match in _MONTH_ONLY_ZH_RE.finditer(source)
    ]
    for _position, kind, source_match in sorted(source_events, key=lambda item: item[0]):
        year = source_match.groupdict().get("year")
        month = source_match.group("month")
        day = source_match.groupdict().get("day")
        remaining_translation = translation[target_cursor:]
        target_match = (
            _english_date_match(remaining_translation, year, month, day)
            if kind == "date" and day
            else _english_month_match(remaining_translation, month)
        )
        if not target_match:
            continue
        source_tokens.extend(token for token in (year, month, day) if token)
        target_tokens.extend(_number_tokens(target_match.group(0)))
        target_cursor += target_match.end()
    return source_tokens, target_tokens


def validate_numbers(
    source: str,
    translation: str,
    *,
    ignore_source_date_components: bool = False,
) -> list[Finding]:
    """Every number in the source must appear in the translation (multiset
    comparison, order-independent, comma/percent normalized).

    Numeric components belonging to a correctly rendered Chinese date are
    consumed first, allowing e.g. ``8月24日`` -> ``August 24`` without
    weakening checks for other occurrences of 8 or 24 in the same segment.
    """
    src = Counter(_norm_number(t) for t in _number_tokens(source))
    tgt = Counter(_norm_number(t) for t in _number_tokens(translation))
    if ignore_source_date_components:
        for match in _DATE_ZH_RE.finditer(source):
            _consume(
                src,
                [
                    token
                    for token in (
                        match.group("year"),
                        match.group("month"),
                        match.group("day"),
                    )
                    if token
                ],
            )
        for match in _MONTH_ONLY_ZH_RE.finditer(source):
            _consume(src, [match.group("month")])
    else:
        date_source_tokens, date_target_tokens = _matched_date_numbers(source, translation)
        _consume(src, date_source_tokens)
        _consume(tgt, date_target_tokens)
    findings: list[Finding] = []
    missing = src - tgt
    for token in sorted(missing):
        findings.append(
            {
                "category": "number",
                "severity": "critical",
                "source_span": token,
                "target_span": "",
                "message": f"源文数字 {token} 未在译文中出现",
                "suggested_fix": f"补译数字 {token}",
            }
        )
    return findings


def validate_dates(source: str, translation: str) -> list[Finding]:
    findings: list[Finding] = []
    for match in _DATE_ZH_RE.finditer(source):
        year = match.group("year")
        month = match.group("month")
        day = match.group("day")
        if not _english_date_match(translation, year, month, day):
            source_date = match.group(0)
            suggested = (
                f"{year}-{int(month):02d}-{int(day):02d}" if year else f"{int(month)}/{int(day)}"
            )
            findings.append(
                {
                    "category": "date",
                    "severity": "critical",
                    "source_span": source_date,
                    "target_span": "",
                    "message": f"源文日期 {source_date} 未在译文中找到对应表达",
                    "suggested_fix": f"补译日期 {suggested}",
                }
            )
    return findings


def _contains_currency_rendering(text: str, renderings: tuple[str, ...]) -> bool:
    normalized = " ".join(text.casefold().split())
    return any(
        re.search(rf"(?<![a-z]){re.escape(rendering)}(?:s)?(?![a-z])", normalized)
        for rendering in renderings
    )


def validate_currencies(source: str, translation: str) -> list[Finding]:
    """Preserve explicit zh->en currency identity as a hard factual anchor."""
    findings: list[Finding] = []
    for source_term, renderings in _ZH_EN_CURRENCIES.items():
        if source_term not in source or _contains_currency_rendering(translation, renderings):
            continue
        preferred = renderings[0]
        findings.append(
            {
                "category": "currency",
                "severity": "critical",
                "source_span": source_term,
                "target_span": "",
                "message": f"源文明示币种“{source_term}”，译文未保留对应币种",
                "suggested_fix": f"保留币种信息，例如 {preferred}",
            }
        )
    return findings


def finding_conflicts_with_currency_anchor(
    source: str,
    current_translation: str,
    finding: Finding,
) -> bool:
    """Reject a model suggestion that would remove/corrupt a correct currency.

    The check is intentionally narrow: it activates only when the finding talks
    about currency (or names the currently correct currency) and supplies a
    concrete replacement. Other semantic/style findings remain untouched.
    """
    suggested = str(finding.get("suggested_fix") or "").strip()
    if not suggested:
        return False
    finding_text = " ".join(
        str(finding.get(key) or "").casefold()
        for key in ("category", "message", "source_span", "target_span", "suggested_fix")
    )
    currency_markers = ("currency", "币种", "货币", "rmb", "yuan", "yen", "dollar", "euro")
    if not any(marker in finding_text for marker in currency_markers):
        return False
    for source_term, renderings in _ZH_EN_CURRENCIES.items():
        if (
            source_term in source
            and _contains_currency_rendering(current_translation, renderings)
            and not _contains_currency_rendering(suggested, renderings)
        ):
            return True
    return False


def validate_brackets(source: str, translation: str) -> list[Finding]:
    """Unbalanced brackets in the translation are a major formatting error."""
    findings: list[Finding] = []
    for zh_open, zh_close, en_open, en_close in _BRACKET_PAIRS:
        if translation.count(en_open) != translation.count(en_close):
            findings.append(
                {
                    "category": "bracket",
                    "severity": "major",
                    "source_span": "",
                    "target_span": f"{en_open}...{en_close}",
                    "message": f"译文括号不配对：{en_open}={translation.count(en_open)} {en_close}={translation.count(en_close)}",
                    "suggested_fix": "检查并补全括号",
                }
            )
    return findings


def validate_quotes(source: str, translation: str) -> list[Finding]:
    findings: list[Finding] = []
    if translation.count('"') % 2 != 0:
        findings.append(
            {
                "category": "quote",
                "severity": "major",
                "source_span": "",
                "target_span": '"',
                "message": "译文引号不配对",
                "suggested_fix": "检查引号开闭",
            }
        )
    return findings


def validate_terminology(source: str, translation: str, glossary: list[dict]) -> list[Finding]:
    """Glossary conformance: when the source contains a mandatory term, the
    translation must contain the mandated rendering."""
    findings: list[Finding] = []
    for entry in glossary:
        # LLM-extracted renderings without paired official evidence are useful
        # hints, not release-blocking law. Historic database terms remain
        # mandatory even before the explicit flag was introduced.
        mandatory = entry.get("mandatory")
        if mandatory is None:
            mandatory = "origin" not in entry or entry.get("origin") in {
                "term_db",
                "official_verified",
            }
        if not mandatory or entry.get("exception"):
            continue
        src_term, target = entry.get("source", ""), entry.get("target", "")
        if (
            src_term
            and src_term in source
            and target
            and target.casefold() not in translation.casefold()
        ):
            findings.append(
                {
                    "category": "terminology",
                    "severity": "critical",
                    "source_span": src_term,
                    "target_span": target,
                    "message": f"术语 {src_term} 未使用规定译法 “{target}”",
                    "suggested_fix": f"改用规定译法 {target}",
                }
            )
    return findings


def validate_term_capitalization(
    source: str, translation: str, glossary: list[dict]
) -> list[Finding]:
    """Enforce casing only for explicitly classified common-term suggestions.

    Advisory terms never force a lexical choice. If the translation does use
    that rendering, however, an explicit proper_name=false gives us a safe,
    deterministic sentence-case contract.
    """
    findings: list[Finding] = []
    for entry in glossary:
        mandatory = entry.get("mandatory")
        if mandatory is None:
            mandatory = "origin" not in entry or entry.get("origin") in {
                "term_db",
                "official_verified",
            }
        # Explicit common terms always carry a casing contract. Binding terms
        # do too: database/curated spellings must survive exactly, including
        # internal capitals, while only the first character may change at the
        # beginning of a sentence.
        if entry.get("proper_name") is not False and not mandatory:
            continue
        source_term = entry.get("source", "")
        target = entry.get("target", "")
        if not source_term or source_term not in source or not target:
            continue
        matches = list(re.finditer(re.escape(target), translation, flags=re.IGNORECASE))
        for match in matches:
            actual = match.group(0)
            expected = target
            if actual == expected:
                continue
            # A rendering that differs only at its first alphabetic character
            # is ordinary sentence case, including after a dateline, colon or
            # opening quotation mark. Context-free QA must not turn that into
            # a release blocker. Internal title-case differences remain
            # deterministic and actionable.
            first_alpha = next(
                (index for index, char in enumerate(expected) if char.isalpha()),
                None,
            )
            if first_alpha is not None:
                initial_variant = (
                    expected[:first_alpha]
                    + expected[first_alpha].swapcase()
                    + expected[first_alpha + 1 :]
                )
                if actual == initial_variant:
                    continue
            findings.append(
                {
                    "category": "capitalization",
                    "severity": "major",
                    "source_span": source_term,
                    "target_span": actual,
                    "message": f"普通术语“{actual}”误用了标题式大写",
                    "suggested_fix": f"按正常句式大小写改为 {expected}",
                }
            )
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
            findings.append(
                {
                    "category": "acronym",
                    "severity": "major",
                    "source_span": token,
                    "target_span": "",
                    "message": f"源文缩写 {token} 未在译文中保留",
                    "suggested_fix": f"保留缩写 {token}",
                }
            )
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
            findings.append(
                {
                    "category": "entity",
                    "severity": "major",
                    "source_span": f"《{title}》",
                    "target_span": "",
                    "message": f"文献名《{title}》可能在译文中缺失（引号/斜体计数不足）",
                    "suggested_fix": f"确认《{title}》已以引号或斜体形式译出",
                }
            )
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
        findings.append(
            {
                "category": "enumeration",
                "severity": "major",
                "source_span": f"{zh_seps + 1} 项并列",
                "target_span": "",
                "message": f"源文 {zh_seps + 1} 项并列结构在译文中疑似丢失（并列连词/逗号不足）",
                "suggested_fix": "检查并列项是否全部译出且结构平行",
            }
        )
    return findings


def run_deterministic(
    source: str,
    translation: str,
    glossary: list[dict] | None = None,
    *,
    source_language: str = "zh",
    target_language: str = "en",
) -> list[Finding]:
    """Run universal checks plus pair-specific checks with known semantics.

    Missing pair-specific rules must never produce false positives for another
    writing system. Model review supplies the language-aware layer while the
    deterministic core remains conservative.
    """
    findings: list[Finding] = []
    findings += validate_numbers(
        source,
        translation,
        ignore_source_date_components=(source_language == "zh" and target_language != "en"),
    )
    findings += validate_brackets(source, translation)
    findings += validate_quotes(source, translation)
    findings += validate_acronyms(source, translation)
    if source_language == "zh" and target_language == "en":
        findings += validate_dates(source, translation)
        findings += validate_currencies(source, translation)
        findings += validate_entities(source, translation)
        findings += validate_enumerations(source, translation)
    if glossary:
        findings += validate_terminology(source, translation, glossary)
        if target_language == "en":
            findings += validate_term_capitalization(source, translation, glossary)
    return findings
