"""Parallel alignment (§11): paragraph_aligner / sentence_aligner /
alignment_scorer. Deterministic dynamic programming over a bilingual
similarity score — no model calls, fully unit-testable.

Score model: log-length-ratio Gaussian (zh chars are ~1.9x denser than en
chars in government prose) + shared-number anchors. Alignment moves allow
1-1, 1-2, 2-1 (sentence level) plus skips with penalty.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

_NUMBER_RE = re.compile(r"\d+(?:,\d{3})*(?:\.\d+)?%?")

EXPECTED_LEN_RATIO = 1.9   # fallback only; document ingestion estimates this per pair
LEN_SIGMA = 0.45
SKIP_PENALTY = 0.35
MERGE_BONUS = 0.05


@dataclass
class Alignment:
    zh_idx: list[int]
    en_idx: list[int]
    score: float


def _numbers(text: str) -> list[str]:
    return sorted(t.replace(",", "").rstrip("%") for t in _NUMBER_RE.findall(text))


def score_pair(
    zh: str,
    en: str,
    *,
    expected_len_ratio: float = EXPECTED_LEN_RATIO,
) -> float:
    """Bilingual similarity in [0, 1]."""
    if not zh or not en:
        return 0.0
    ratio = len(en) / max(len(zh), 1)
    expected = max(expected_len_ratio, 1e-6)
    len_score = math.exp(-((math.log(max(ratio, 1e-6)) - math.log(expected)) ** 2)
                         / (2 * LEN_SIGMA**2))
    zh_nums, en_nums = _numbers(zh), _numbers(en)
    if zh_nums:
        shared = sum(1 for n in en_nums if n in zh_nums)
        num_score = shared / len(zh_nums)
        return round(0.55 * len_score + 0.45 * num_score, 4)
    return round(len_score, 4)


def align_sequences(
    zh_items: list[str],
    en_items: list[str],
    *,
    allow_merges: bool,
    min_score: float = 0.15,
    expected_len_ratio: float = EXPECTED_LEN_RATIO,
) -> list[Alignment]:
    """DP global alignment. Returns 1-1 / 1-2 / 2-1 alignments whose pair
    score clears min_score; skipped items are dropped (reported by caller)."""
    n, m = len(zh_items), len(en_items)
    NEG = -1e9
    # moves: (take_zh, take_en, bonus)
    moves = [(1, 1, 0.0)]
    if allow_merges:
        # Official Chinese and English editions do not always share paragraph
        # boundaries. Support the common 1:2/2:1 cases as well as occasional
        # 1:3/3:1 and 2:2 layouts instead of forcing a plausible-looking but
        # semantically shifted 1:1 match.
        moves.extend([
            (1, 2, MERGE_BONUS),
            (2, 1, MERGE_BONUS),
            (1, 3, MERGE_BONUS * 2),
            (3, 1, MERGE_BONUS * 2),
            (2, 2, MERGE_BONUS),
        ])
    dp = [[(NEG, None)] * (m + 1) for _ in range(n + 1)]
    dp[0][0] = (0.0, None)
    for i in range(n + 1):
        for j in range(m + 1):
            if i == 0 and j == 0:
                continue
            best = (NEG, None)
            if i > 0:  # skip zh
                cand = dp[i - 1][j][0] - SKIP_PENALTY
                if cand > best[0]:
                    best = (cand, (i - 1, j, None))
            if j > 0:  # skip en
                cand = dp[i][j - 1][0] - SKIP_PENALTY
                if cand > best[0]:
                    best = (cand, (i, j - 1, None))
            for dz, de, bonus in moves:
                if i >= dz and j >= de:
                    zh = "".join(zh_items[i - dz:i])
                    en = " ".join(en_items[j - de:j])
                    cand = (
                        dp[i - dz][j - de][0]
                        + score_pair(zh, en, expected_len_ratio=expected_len_ratio)
                        + bonus
                    )
                    if cand > best[0]:
                        best = (cand, (i - dz, j - de, (dz, de)))
            dp[i][j] = best
    alignments: list[Alignment] = []
    i, j = n, m
    while i > 0 or j > 0:
        _, back = dp[i][j]
        if back is None:
            break
        pi, pj, move = back
        if move:
            dz, de = move
            zh = "".join(zh_items[pi:i]) if dz > 1 else zh_items[pi]
            en = " ".join(en_items[pj:j]) if de > 1 else en_items[pj]
            score = score_pair(zh, en, expected_len_ratio=expected_len_ratio)
            if score >= min_score:
                alignments.append(Alignment(list(range(pi, i)), list(range(pj, j)), score))
        i, j = pi, pj
    alignments.reverse()
    return alignments
