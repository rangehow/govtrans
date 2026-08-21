"""Translation Memory search (§12/§15): BM25 lexical + metadata filters +
authority rerank. Vector/semantic leg activates with pgvector (E06);
the ranking hook is isolated so hybrid fusion lands in one place.
"""
from __future__ import annotations

from sqlalchemy import select

from apps.api.db import SessionLocal
from services.retrieval.bm25 import BM25, tokenize
from services.retrieval.models import AUTHORITY_LEVELS, TMEntry

_CANDIDATE_LIMIT = 500


def _authority_boost(authority: str) -> float:
    try:
        # earlier level = higher trust -> larger boost
        return 1.0 + (len(AUTHORITY_LEVELS) - AUTHORITY_LEVELS.index(authority)) * 0.05
    except ValueError:
        return 1.0


def tm_search(
    text: str,
    *,
    document_type: str | None = None,
    domain: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    with SessionLocal() as session:
        stmt = select(TMEntry)
        if document_type:
            stmt = stmt.where(TMEntry.document_type == document_type)
        if domain:
            stmt = stmt.where(TMEntry.domain == domain)
        rows = session.execute(stmt.limit(_CANDIDATE_LIMIT)).scalars().all()
        records = [
            {"id": r.id, "source": r.source, "target": r.target,
             "source_document": r.source_document, "url": r.url, "authority": r.authority}
            for r in rows
        ]
    if not records:
        return []
    ranker = BM25([tokenize(r["source"]) for r in records])
    results = []
    for idx, score in ranker.rank(text, top_k=top_k * 2):
        rec = records[idx]
        final = score * _authority_boost(rec["authority"])
        results.append((final, score, rec))
    results.sort(key=lambda item: item[0], reverse=True)
    return [
        {**rec, "score": round(final, 3), "bm25": round(raw, 3)}
        for final, raw, rec in results[:top_k]
    ]
