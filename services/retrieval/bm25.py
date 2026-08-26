"""BM25 lexical ranking for Translation Memory (§12/§15).

Pure-Python Okapi BM25 over TM source texts. Chinese has no whitespace
tokenization, so we use bigrams for zh and word tokens for en — a standard
cheap trick that works well enough until pgvector semantic ranking lands
(hybrid = bm25 + metadata + [vector later] + authority rerank).
"""

from __future__ import annotations

import math
import unicodedata
from collections import Counter

_NO_SPACE_SCRIPT_NAMES = (
    "CJK",
    "HIRAGANA",
    "KATAKANA",
    "THAI",
    "LAO",
    "KHMER",
    "MYANMAR",
)


def _no_space_script(char: str) -> bool:
    name = unicodedata.name(char, "")
    return any(marker in name for marker in _NO_SPACE_SCRIPT_NAMES)


def tokenize(text: str) -> list[str]:
    """Tokenize spacing scripts as words and unsegmented scripts as bigrams."""
    tokens: list[str] = []
    word_run: list[str] = []
    compact_run: list[str] = []

    def flush_word() -> None:
        if word_run:
            tokens.append("".join(word_run).casefold())
            word_run.clear()

    def flush_compact() -> None:
        if not compact_run:
            return
        value = "".join(compact_run)
        if len(value) == 1:
            tokens.append(value)
        else:
            tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
        compact_run.clear()

    for char in text:
        category = unicodedata.category(char)
        if not category.startswith(("L", "M", "N")):
            flush_word()
            flush_compact()
        elif _no_space_script(char):
            flush_word()
            compact_run.append(char)
        else:
            flush_compact()
            word_run.append(char)
    flush_word()
    flush_compact()
    return tokens


class BM25:
    def __init__(self, corpus: list[list[str]], *, k1: float = 1.5, b: float = 0.75) -> None:
        self.k1, self.b = k1, b
        self.doc_tokens = corpus
        self.doc_len = [max(len(d), 1) for d in corpus]
        self.avgdl = sum(self.doc_len) / max(len(corpus), 1)
        self.df: Counter[str] = Counter()
        self.tf: list[Counter[str]] = []
        for doc in corpus:
            counts = Counter(doc)
            self.tf.append(counts)
            for token in counts:
                self.df[token] += 1
        self.n_docs = len(corpus)

    def _idf(self, token: str) -> float:
        n = self.df.get(token, 0)
        return math.log(1 + (self.n_docs - n + 0.5) / (n + 0.5))

    def score(self, query: list[str], doc_idx: int) -> float:
        total = 0.0
        dl = self.doc_len[doc_idx]
        for token in query:
            tf = self.tf[doc_idx].get(token, 0)
            if tf == 0:
                continue
            idf = self._idf(token)
            total += (
                idf
                * (tf * (self.k1 + 1))
                / (tf + self.k1 * (1 - self.b + self.b * dl / self.avgdl))
            )
        return total

    def rank(self, query_text: str, top_k: int) -> list[tuple[int, float]]:
        query = tokenize(query_text)
        scored = [(i, self.score(query, i)) for i in range(self.n_docs)]
        scored = [(i, s) for i, s in scored if s > 0]
        scored.sort(key=lambda item: item[1], reverse=True)
        return scored[:top_k]
