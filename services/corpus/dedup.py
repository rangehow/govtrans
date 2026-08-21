"""Deduplicator (§11): normalized content hashing for documents and pairs."""
from __future__ import annotations

import hashlib
from typing import Any


def normalize(text: str) -> str:
    # all whitespace removed: dedup keys must be immune to spacing differences
    # (spurious spaces in CJK text, collapsed runs in HTML extraction)
    return "".join(text.split()).lower()


def content_hash(text: str) -> str:
    return hashlib.sha256(normalize(text).encode("utf-8")).hexdigest()


def pair_hash(zh_text: str, en_text: str) -> str:
    return content_hash(zh_text + "\x00" + en_text)


class PairDeduplicator:
    """Keeps the highest-scoring copy of each (zh, en) pair, with metadata."""

    def __init__(self) -> None:
        self._best: dict[str, int] = {}  # pair_hash -> index in kept list
        self.kept: list[tuple[str, str, float, dict[str, Any]]] = []
        self.dropped = 0

    def add(self, zh_text: str, en_text: str, score: float, **meta: Any) -> bool:
        """Returns True when the pair is new or replaces a lower-scored copy."""
        key = pair_hash(zh_text, en_text)
        if key in self._best:
            self.dropped += 1
            idx = self._best[key]
            if score > self.kept[idx][2]:
                self.kept[idx] = (zh_text, en_text, score, meta)
                return True
            return False
        self._best[key] = len(self.kept)
        self.kept.append((zh_text, en_text, score, meta))
        return True
