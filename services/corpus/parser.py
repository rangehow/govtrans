"""Document parser: HTML -> ordered structure blocks (stdlib only).

Structure block kinds: heading / paragraph / list_item / table_cell.
Both raw HTML and this structure are persisted (provenance, §11).
"""
from __future__ import annotations

import re
from html.parser import HTMLParser

_BLOCK_TAGS = {
    "h1": "heading", "h2": "heading", "h3": "heading", "h4": "heading",
    "p": "paragraph",
    "li": "list_item",
    "td": "table_cell", "th": "table_cell",
}
_SKIP_TAGS = {"script", "style", "noscript", "iframe", "nav", "footer", "header", "form"}


class _StructureParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.blocks: list[dict] = []
        self.title: str | None = None
        self._stack: list[str] = []
        self._buf: list[str] = []
        self._current_kind: str | None = None
        self._in_title = False
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs) -> None:
        self._stack.append(tag)
        if tag in _SKIP_TAGS:
            self._skip_depth += 1
        if tag == "title":
            self._in_title = True
        if tag in _BLOCK_TAGS and self._current_kind is None:
            self._current_kind = _BLOCK_TAGS[tag]
            self._buf = []

    def handle_endtag(self, tag: str) -> None:
        if self._stack and self._stack[-1] == tag:
            self._stack.pop()
        if tag in _SKIP_TAGS and self._skip_depth > 0:
            self._skip_depth -= 1
        if tag == "title":
            self._in_title = False
        if tag in _BLOCK_TAGS and self._current_kind == _BLOCK_TAGS.get(tag):
            text = " ".join("".join(self._buf).split())
            if text:
                self.blocks.append({"kind": self._current_kind, "text": text})
            self._current_kind = None
            self._buf = []

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title and self.title is None:
            title = data.strip()
            if title:
                self.title = title
        if self._current_kind is not None:
            self._buf.append(data)


def parse_html(html: str) -> tuple[list[dict], str | None]:
    """Returns (structure_blocks, title)."""
    parser = _StructureParser()
    parser.feed(html)
    parser.close()
    return parser.blocks, parser.title


def blocks_to_text(blocks: list[dict]) -> str:
    return "\n".join(b["text"] for b in blocks if b["kind"] != "table_cell")


def split_sentences(text: str, lang: str) -> list[str]:
    """Deterministic sentence splitter for zh/en parallel text."""
    text = " ".join(text.split())
    if not text:
        return []
    if lang == "zh":
        parts = re.split(r"(?<=[。！？；])", text)
    else:
        # split after . ! ? followed by space+capital or end; keep decimals/abbrevs cheap-safe
        parts = re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'])", text)
    return [p.strip() for p in parts if p and p.strip()]


def extract_metadata(html: str, url: str | None, title: str | None) -> dict:
    """Metadata extractor: publish date + source site from common gov-page
    patterns. Deterministic regexes over the raw HTML (provenance kept)."""
    meta: dict = {"url": url, "title": title}
    date = re.search(r"(20\d{2})\s*[-年/]\s*(\d{1,2})\s*[-月/]\s*(\d{1,2})", html[:5000])
    if date:
        meta["publish_date"] = f"{date.group(1)}-{int(date.group(2)):02d}-{int(date.group(3)):02d}"
    source = re.search(r"(?:来源|Source)[:：]\s*([^<\s]{2,40})", html[:8000])
    if source:
        meta["source"] = source.group(1)
    if url:
        host = re.sub(r"^https?://", "", url).split("/")[0]
        meta["host"] = host
    return meta
