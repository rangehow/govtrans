"""Deterministic evaluation metrics (§36). LLM-judged metrics (faithfulness,
style) plug in later; everything here is computable offline and unit-tested.

- chrf: character n-gram F-score (β=2, n=1..6) vs reference — standard MT
  surface metric.
- numbers_score: fraction of source numbers preserved in the hypothesis.
- term_score: fraction of mandatory glossary terms rendered correctly.
"""
from __future__ import annotations

from collections import Counter

from services.quality import validators


def chrf(hypothesis: str, reference: str, *, max_n: int = 6, beta: float = 2.0) -> float:
    if not hypothesis or not reference:
        return 0.0
    hyp = " ".join(hypothesis.split())
    ref = " ".join(reference.split())
    scores = []
    for n in range(1, max_n + 1):
        hyp_ng = Counter(hyp[i : i + n] for i in range(len(hyp) - n + 1))
        ref_ng = Counter(ref[i : i + n] for i in range(len(ref) - n + 1))
        overlap = sum((hyp_ng & ref_ng).values())
        p = overlap / max(sum(hyp_ng.values()), 1)
        r = overlap / max(sum(ref_ng.values()), 1)
        if p == 0 and r == 0:
            scores.append(0.0)
        else:
            scores.append((1 + beta**2) * p * r / max(beta**2 * p + r, 1e-12))
    return round(sum(scores) / len(scores), 4)


def numbers_score(source: str, hypothesis: str) -> float:
    findings = validators.validate_numbers(source, hypothesis)
    total = len(validators._NUMBER_RE.findall(source))
    if total == 0:
        return 1.0
    return round(max(0.0, 1.0 - len(findings) / total), 4)


def term_score(source: str, hypothesis: str, glossary: list[dict]) -> float:
    relevant = [g for g in glossary if g.get("source") and g["source"] in source]
    if not relevant:
        return 1.0
    findings = validators.validate_terminology(source, hypothesis, relevant)
    return round(max(0.0, 1.0 - len(findings) / len(relevant)), 4)


def aggregate(item_metrics: list[dict]) -> dict:
    if not item_metrics:
        return {}
    keys = item_metrics[0].keys()
    return {
        k: round(sum(m[k] for m in item_metrics) / len(item_metrics), 4)
        for k in keys
        if isinstance(item_metrics[0][k], (int, float))
    }
