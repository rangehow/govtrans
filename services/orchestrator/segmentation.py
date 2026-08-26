"""Structure-aware source segmentation for model-safe translation units.

Paragraphs remain the primary unit. Only paragraphs that exceed the configured
model-safe size are split, first at sentence boundaries and then at punctuation.
The function is deterministic so a restarted run recreates the same segments.
"""

from __future__ import annotations

import re
import unicodedata


_SENTENCE_BOUNDARY = re.compile(r"(?<=[。！？；!?;؟؛।॥])|(?<=[.])\s+(?=\S)")
_SOFT_BREAKS = ("，", ",", "،", "、", "：", ":", "؛", ";", " ")
_TERMINAL_PUNCTUATION = re.compile(r"[。！？!?;؟؛।॥]”?》?$|[.]\s*$")
_HEADING_PREFIX = re.compile(
    r"^(?:第[\u4e00-\u9fff零○一二三四五六七八九十百千两\d]+章|"
    r"[一二三四五六七八九十百千]+[、.]|"
    r"[（(][一二三四五六七八九十\d]+[）)]|"
    r"\d+(?:\.\d+)*[.、)）])"
)
_LIST_PREFIX = re.compile(
    r"^(?:[-–—•·●○▪▫*]　?\s*|"
    r"\d+[.)）]　?\s*|[（(]?\d+[）)]　?\s*)"
)

_NO_SPACE_SCRIPT_NAMES = (
    "CJK",
    "HIRAGANA",
    "KATAKANA",
    "THAI",
    "LAO",
    "KHMER",
    "MYANMAR",
)


def _uses_no_interword_space(char: str) -> bool:
    name = unicodedata.name(char, "")
    return any(marker in name for marker in _NO_SPACE_SCRIPT_NAMES)


def _line_join_separator(left: str, right: str) -> str:
    """Preserve word boundaries for all spacing scripts, not only ASCII."""
    if not left or not right:
        return ""
    if _uses_no_interword_space(left) or _uses_no_interword_space(right):
        return ""
    if left in "([{\u300a〈【“'\"" or right in ")]},.!?;:》〉】”'،؛؟":
        return ""
    return " "


def infer_block_kind(text: str, *, index: int = 0) -> str:
    """Infer a presentation/export role without changing persisted text.

    The classification is intentionally conservative: prose remains a normal
    paragraph unless it has a clear structural signal. This gives the web and
    DOCX exporter useful hierarchy while keeping segmentation deterministic.
    """
    compact = " ".join(text.split())
    if not compact:
        return "paragraph"
    if _LIST_PREFIX.match(compact):
        return "list"
    if _HEADING_PREFIX.match(compact) and len(compact) <= 120:
        return "heading"
    if len(compact) <= 64 and not _TERMINAL_PUNCTUATION.search(compact):
        return "title" if index == 0 else "heading"
    return "paragraph"


def _join_soft_wrapped_lines(text: str) -> list[str]:
    """Restore paragraphs broken only by PDF/editor visual line wrapping.

    Blank lines and complete sentences remain hard paragraph boundaries.
    A line ending mid-sentence is joined to the next line, which prevents a
    pasted PDF from becoming dozens of tiny translation/display units.
    """
    logical: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            logical.append(current.strip())
            current = ""

    for raw_line in text.split("\n"):
        line = raw_line.strip()
        if not line:
            flush()
            continue
        if not current:
            current = line
            continue

        previous_complete = bool(_TERMINAL_PUNCTUATION.search(current))
        # A plain short first line may simply be a PDF wrap, so only explicit
        # heading/list patterns form a hard boundary without a blank line.
        structural_boundary = any(
            pattern.match(value)
            for value in (current, line)
            for pattern in (_HEADING_PREFIX, _LIST_PREFIX)
        )
        if previous_complete or structural_boundary:
            flush()
            current = line
            continue

        separator = _line_join_separator(current[-1:], line[:1])
        current = f"{current}{separator}{line}"

    flush()
    return logical


def _hard_wrap(text: str, max_chars: int) -> list[str]:
    chunks: list[str] = []
    remaining = text.strip()
    while len(remaining) > max_chars:
        lower_bound = max_chars // 2
        split_at = -1
        for marker in _SOFT_BREAKS:
            candidate = remaining.rfind(marker, lower_bound, max_chars + 1)
            split_at = max(split_at, candidate + (1 if candidate >= 0 else 0))
        if split_at <= 0:
            split_at = max_chars
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def split_source_text(text: str, *, max_chars: int = 900) -> list[str]:
    """Return ordered, non-empty translation units without silent truncation."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    paragraphs = _join_soft_wrapped_lines(normalized)
    segments: list[str] = []

    for paragraph in paragraphs:
        if len(paragraph) <= max_chars:
            segments.append(paragraph)
            continue

        sentences = [part.strip() for part in _SENTENCE_BOUNDARY.split(paragraph) if part.strip()]
        current = ""
        for sentence in sentences:
            if len(sentence) > max_chars:
                if current:
                    segments.append(current)
                    current = ""
                segments.extend(_hard_wrap(sentence, max_chars))
                continue
            separator = _line_join_separator(current[-1:], sentence[:1]) if current else ""
            candidate = f"{current}{separator}{sentence}" if current else sentence
            if len(candidate) <= max_chars:
                current = candidate
            else:
                segments.append(current)
                current = sentence
        if current:
            segments.append(current)

    return segments
