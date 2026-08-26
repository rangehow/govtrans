"""Official reference search for translation and review.

Two evidence tiers share one advisory retrieval path:
- human-verified ``translation_memory`` rows;
- high-confidence official corpus alignments, active automatically.

Neither tier is a binding terminology contract. Results are shown to the
translator/reviewers as auditable examples, while terminology remains the
only lexical hard constraint.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from threading import Lock

from sqlalchemy import and_, or_, select

from apps.api.db import SessionLocal
from services.corpus.models import AlignedPair, CorpusDocument, DocumentPair
from services.languages import resolve_language_pair
from services.retrieval.bm25 import BM25, tokenize
from services.retrieval.models import AUTHORITY_LEVELS, TMEntry

AUTO_REFERENCE_MIN_SCORE = 0.85
_CACHE_TTL_SECONDS = 60.0


@dataclass
class _ReferenceIndex:
    loaded_at: float
    records: list[dict]
    ranker: BM25 | None


_INDEX_CACHE: dict[tuple[str, str, str | None, str | None], _ReferenceIndex] = {}
_INDEX_LOCK = Lock()


def _authority_boost(authority: str) -> float:
    try:
        # earlier level = higher trust -> larger boost
        return 1.0 + (len(AUTHORITY_LEVELS) - AUTHORITY_LEVELS.index(authority)) * 0.05
    except ValueError:
        return 1.0


def invalidate_reference_indexes() -> None:
    """Make newly imported, corrected or excluded evidence visible at once."""
    with _INDEX_LOCK:
        _INDEX_CACHE.clear()


def _load_reference_records(
    source_language: str,
    target_language: str,
    document_type: str | None,
    domain: str | None,
) -> list[dict]:
    records: list[dict] = []
    with SessionLocal() as session:
        # Select only retrieval fields. Corpus documents can contain megabytes
        # of raw HTML/text; selecting ORM entities in a sentence-level join
        # repeated that payload thousands of times and made a cold index take
        # tens of seconds to load.
        tm_stmt = select(
            TMEntry.id,
            TMEntry.source,
            TMEntry.target,
            TMEntry.source_language,
            TMEntry.target_language,
            TMEntry.source_document,
            TMEntry.url,
            TMEntry.authority,
            TMEntry.score_hint,
        ).where(
            or_(
                and_(
                    TMEntry.source_language == source_language,
                    TMEntry.target_language == target_language,
                ),
                and_(
                    TMEntry.source_language == target_language,
                    TMEntry.target_language == source_language,
                ),
            )
        )
        if document_type:
            # NULL is retained as a compatibility fallback for old manually
            # verified rows; all new reviews inherit corpus metadata.
            tm_stmt = tm_stmt.where(
                or_(
                    TMEntry.document_type == document_type,
                    TMEntry.document_type.is_(None),
                )
            )
        if domain:
            tm_stmt = tm_stmt.where(or_(TMEntry.domain == domain, TMEntry.domain.is_(None)))
        for row in session.execute(tm_stmt):
            reverse = (
                row.source_language == target_language and row.target_language == source_language
            )
            records.append(
                {
                    "id": row.id,
                    "source": row.target if reverse else row.source,
                    "target": row.source if reverse else row.target,
                    "source_language": source_language,
                    "target_language": target_language,
                    "source_document": row.source_document,
                    "url": row.url,
                    "authority": row.authority,
                    "kind": "verified_memory",
                    "usage": "advisory",
                    "alignment_score": row.score_hint,
                }
            )

        if {source_language, target_language} == {"zh", "en"}:
            corpus_stmt = (
                select(
                    AlignedPair.id,
                    AlignedPair.zh_text,
                    AlignedPair.en_text,
                    AlignedPair.score,
                    CorpusDocument.title,
                    CorpusDocument.url,
                )
                .join(DocumentPair, DocumentPair.id == AlignedPair.pair_id)
                .join(CorpusDocument, CorpusDocument.id == DocumentPair.zh_doc_id)
                .where(
                    AlignedPair.level == "sentence",
                    AlignedPair.score >= AUTO_REFERENCE_MIN_SCORE,
                    AlignedPair.status != "rejected",
                    AlignedPair.tm_entry_id.is_(None),
                )
                .order_by(AlignedPair.score.desc())
            )
            if document_type:
                corpus_stmt = corpus_stmt.where(CorpusDocument.document_type == document_type)
            if domain:
                corpus_stmt = corpus_stmt.where(
                    or_(CorpusDocument.domain == domain, CorpusDocument.domain.is_(None))
                )
            reverse_corpus = source_language == "en"
            for row in session.execute(corpus_stmt):
                records.append(
                    {
                        "id": row.id,
                        "source": row.en_text if reverse_corpus else row.zh_text,
                        "target": row.zh_text if reverse_corpus else row.en_text,
                        "source_language": source_language,
                        "target_language": target_language,
                        "source_document": row.title or "国新办官方双语文档",
                        "url": row.url,
                        "authority": "official_aligned",
                        "kind": "official_corpus",
                        "usage": "advisory",
                        "alignment_score": round(row.score, 3),
                    }
                )

    # Cross-document boilerplate is common. Keep the higher-authority record
    # first so repeated sentences cannot crowd out diverse references.
    unique: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for record in records:
        key = (record["source"], record["target"])
        if key in seen:
            continue
        seen.add(key)
        unique.append(record)
    return unique


def _reference_index(
    source_language: str,
    target_language: str,
    document_type: str | None,
    domain: str | None,
) -> _ReferenceIndex:
    key = (source_language, target_language, document_type, domain)
    now = time.monotonic()
    cached = _INDEX_CACHE.get(key)
    if cached and now - cached.loaded_at < _CACHE_TTL_SECONDS:
        return cached
    with _INDEX_LOCK:
        cached = _INDEX_CACHE.get(key)
        if cached and now - cached.loaded_at < _CACHE_TTL_SECONDS:
            return cached
        records = _load_reference_records(source_language, target_language, document_type, domain)
        index = _ReferenceIndex(
            loaded_at=now,
            records=records,
            ranker=BM25([tokenize(record["source"]) for record in records]) if records else None,
        )
        _INDEX_CACHE[key] = index
        return index


def tm_search(
    text: str,
    *,
    source_language: str = "zh",
    target_language: str = "en",
    document_type: str | None = None,
    domain: str | None = None,
    top_k: int = 5,
) -> list[dict]:
    source_language, target_language = resolve_language_pair(source_language, target_language)
    index = _reference_index(source_language, target_language, document_type, domain)
    if not index.ranker:
        return []
    results = []
    for idx, score in index.ranker.rank(text, top_k=top_k * 3):
        rec = index.records[idx]
        alignment_score = float(rec.get("alignment_score") or 1.0)
        quality_boost = 0.9 + min(1.0, alignment_score) * 0.1
        final = score * _authority_boost(rec["authority"]) * quality_boost
        results.append((final, score, rec))
    results.sort(key=lambda item: item[0], reverse=True)
    return [
        {**rec, "score": round(final, 3), "bm25": round(raw, 3)}
        for final, raw, rec in results[:top_k]
    ]
