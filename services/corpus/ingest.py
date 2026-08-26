"""Corpus ingestion pipeline (§11, E04/E05).

ingest_document_pair(): raw zh/en inputs -> parse -> metadata -> store docs
(raw provenance kept) -> pair -> paragraph align -> sentence align ->
dedupe -> persist AlignedPair rows. Sentence pairs scoring >= MIN_TM_SCORE
are promoted into translation_memory with authority + provenance backrefs.

Inputs can be local files/raw strings (offline-proof) or URLs via the
crawler (online). The same code path serves SCIO crawls and local imports.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select

from apps.api.db import SessionLocal
from services.corpus.aligner import align_sequences
from services.corpus.dedup import PairDeduplicator, content_hash, pair_hash
from services.corpus.models import (
    CURRENT_ALIGNMENT_VERSION,
    AlignedPair,
    CorpusDocument,
    DocumentPair,
)
from services.corpus.parser import blocks_to_text, extract_metadata, parse_html, split_sentences
from services.retrieval.models import TMEntry

logger = logging.getLogger("govtrans.corpus.ingest")

MIN_TM_SCORE = 0.5  # pairs below this stay in corpus but are not promoted


def _invalidate_reference_cache() -> None:
    # Local import avoids coupling the persistence models to retrieval startup.
    from services.retrieval.tm import invalidate_reference_indexes

    invalidate_reference_indexes()


@dataclass
class IngestResult:
    zh_doc_id: str
    en_doc_id: str
    pair_id: str
    paragraph_pairs: int = 0
    sentence_pairs: int = 0
    dedup_dropped: int = 0
    promoted_to_tm: int = 0
    warnings: list[str] = field(default_factory=list)


def _store_document(
    session, *, raw: str, is_html: bool, lang: str, url: str | None,
    document_type: str | None, domain: str | None,
) -> CorpusDocument:
    """Parse + persist one document. Idempotent via content_hash."""
    if is_html:
        structure, title = parse_html(raw)
        text = blocks_to_text(structure)
    else:
        structure = [{"kind": "paragraph", "text": p.strip()} for p in raw.split("\n") if p.strip()]
        title = None
        text = raw.strip()
    digest = content_hash(text)
    existing = session.execute(
        select(CorpusDocument).where(CorpusDocument.content_hash == digest)
    ).scalar_one_or_none()
    if existing:
        return existing
    doc = CorpusDocument(
        url=url, title=title, lang=lang, document_type=document_type, domain=domain,
        raw_html=raw if is_html else None, raw_text=text, structure=structure,
        doc_metadata=extract_metadata(raw if is_html else "", url, title),
        content_hash=digest,
    )
    session.add(doc)
    session.flush()
    return doc


def _body_paragraphs(structure: list[dict], lang: str) -> list[str]:
    """Drop cover/contents blocks when the real body marker is repeated.

    SCIO full-text pages contain a linked table of contents followed by the
    same ``前言``/``Preface`` marker at the start of the actual text. Treating
    both as prose shifts every later match while still producing deceptively
    good length scores.
    """
    paragraphs = [
        block["text"]
        for block in structure
        if block["kind"] in ("paragraph", "heading", "list_item")
    ]
    markers = {"前言", "序言", "引言"} if lang == "zh" else {"preface", "foreword", "introduction"}
    marker_indexes = [
        index
        for index, text in enumerate(paragraphs)
        if text.strip().casefold().rstrip(":：") in markers
    ]
    if len(marker_indexes) >= 2:
        return paragraphs[marker_indexes[1]:]
    return paragraphs


def _length_ratio(zh: str, en: str) -> float:
    # Real official editions vary substantially by paragraph granularity.
    # Clamping prevents a stray navigation block from defining the score model.
    return min(6.5, max(1.2, len(en) / max(len(zh), 1)))


def _collect_pairs(zh_doc, en_doc) -> PairDeduplicator:
    """Paragraph alignment, then sentence alignment within each aligned
    paragraph; everything flows through the deduplicator."""
    dedup = PairDeduplicator()
    zh_paras = _body_paragraphs(zh_doc.structure, "zh")
    en_paras = _body_paragraphs(en_doc.structure, "en")
    document_ratio = _length_ratio("".join(zh_paras), " ".join(en_paras))
    for pa in align_sequences(
        zh_paras,
        en_paras,
        allow_merges=True,
        expected_len_ratio=document_ratio,
    ):
        p_idx = pa.zh_idx[0]
        zh_p = "".join(zh_paras[index] for index in pa.zh_idx)
        en_p = " ".join(en_paras[index] for index in pa.en_idx)
        zh_sents = split_sentences(zh_p, "zh")
        en_sents = split_sentences(en_p, "en")
        if len(zh_sents) == 1 and len(en_sents) == 1:
            # single-sentence paragraph: sentence level is the same text pair;
            # keep only the TM-ready granularity
            dedup.add(zh_p, en_p, pa.score, level="sentence", idx=f"{p_idx}.0")
            continue
        dedup.add(zh_p, en_p, pa.score, level="paragraph", idx=str(p_idx))
        local_ratio = _length_ratio(zh_p, en_p)
        for sa in align_sequences(
            zh_sents,
            en_sents,
            allow_merges=True,
            expected_len_ratio=local_ratio,
        ):
            zh_s = "".join(zh_sents[i] for i in sa.zh_idx)
            en_s = " ".join(en_sents[i] for i in sa.en_idx)
            dedup.add(zh_s, en_s, sa.score,
                      level="sentence", idx=f"{p_idx}.{sa.zh_idx[0]}")
    return dedup


def ingest_document_pair(
    *,
    zh_source: str,
    en_source: str,
    is_html: bool = False,
    zh_url: str | None = None,
    en_url: str | None = None,
    document_type: str | None = None,
    domain: str | None = None,
    match_method: str = "cli",
    promote: bool = False,
) -> IngestResult:
    with SessionLocal() as session:
        zh_doc = _store_document(session, raw=zh_source, is_html=is_html, lang="zh",
                                 url=zh_url, document_type=document_type, domain=domain)
        en_doc = _store_document(session, raw=en_source, is_html=is_html, lang="en",
                                 url=en_url, document_type=document_type, domain=domain)
        existing_pair = session.execute(
            select(DocumentPair).where(
                DocumentPair.zh_doc_id == zh_doc.id,
                DocumentPair.en_doc_id == en_doc.id,
            )
        ).scalar_one_or_none()
        pair = existing_pair
        preserved_rows: list[AlignedPair] = []
        if existing_pair:
            rows = session.execute(
                select(AlignedPair)
                .where(AlignedPair.pair_id == existing_pair.id)
                .order_by(AlignedPair.idx)
            ).scalars().all()
            stale = existing_pair.alignment_version != CURRENT_ALIGNMENT_VERSION
            if stale:
                previous_version = existing_pair.alignment_version
                for row in rows:
                    if row.status != "auto" or row.tm_entry_id:
                        preserved_rows.append(row)
                    else:
                        session.delete(row)
                session.flush()
                existing_pair.alignment_version = CURRENT_ALIGNMENT_VERSION
                existing_pair.match_method = match_method
                logger.info(
                    "rebuilding pair %s from alignment version %s to %s",
                    existing_pair.id,
                    previous_version,
                    CURRENT_ALIGNMENT_VERSION,
                )
            else:
                result = IngestResult(
                    zh_doc_id=zh_doc.id,
                    en_doc_id=en_doc.id,
                    pair_id=existing_pair.id,
                    paragraph_pairs=sum(row.level == "paragraph" for row in rows),
                    sentence_pairs=sum(row.level == "sentence" for row in rows),
                    warnings=[
                        "identical document pair already existed; reused prior evidence"
                    ],
                )
                if promote:
                    result.promoted_to_tm = _promote_rows(
                        session,
                        rows,
                        zh_doc=zh_doc,
                        zh_url=zh_url,
                        document_type=document_type,
                        domain=domain,
                        pair_id=existing_pair.id,
                    )
                session.commit()
                _invalidate_reference_cache()
                return result
        else:
            pair = DocumentPair(
                zh_doc_id=zh_doc.id,
                en_doc_id=en_doc.id,
                match_method=match_method,
                match_confidence=1.0,
                alignment_version=CURRENT_ALIGNMENT_VERSION,
            )
            session.add(pair)
            session.flush()

        dedup = _collect_pairs(zh_doc, en_doc)
        result = IngestResult(
            zh_doc_id=zh_doc.id,
            en_doc_id=en_doc.id,
            pair_id=pair.id,
            paragraph_pairs=sum(row.level == "paragraph" for row in preserved_rows),
            sentence_pairs=sum(row.level == "sentence" for row in preserved_rows),
            dedup_dropped=dedup.dropped,
        )
        provenance = {"zh_doc": zh_doc.id, "en_doc": en_doc.id,
                      "zh_url": zh_url, "en_url": en_url}
        rows: list[AlignedPair] = []
        preserved_keys = {pair_hash(row.zh_text, row.en_text) for row in preserved_rows}
        for zh_text, en_text, score, meta in dedup.kept:
            if pair_hash(zh_text, en_text) in preserved_keys:
                result.dedup_dropped += 1
                continue
            rows.append(AlignedPair(
                pair_id=pair.id, level=meta["level"], idx=meta["idx"],
                zh_text=zh_text, en_text=en_text, score=score, provenance=provenance,
            ))
            if meta["level"] == "paragraph":
                result.paragraph_pairs += 1
            else:
                result.sentence_pairs += 1
        session.add_all(rows)
        session.flush()

        if promote:
            result.promoted_to_tm = _promote_rows(
                session,
                rows,
                zh_doc=zh_doc,
                zh_url=zh_url,
                document_type=document_type,
                domain=domain,
                pair_id=pair.id,
            )
        session.commit()
    _invalidate_reference_cache()
    logger.info("ingested pair %s: %d paragraphs, %d sentences, %d promoted, %d dedup-dropped",
                result.pair_id, result.paragraph_pairs, result.sentence_pairs,
                result.promoted_to_tm, result.dedup_dropped)
    return result


def _promote_rows(
    session,
    rows: list[AlignedPair],
    *,
    zh_doc: CorpusDocument,
    zh_url: str | None,
    document_type: str | None,
    domain: str | None,
    pair_id: str,
) -> int:
    """Explicitly publish eligible aligned rows into TM, idempotently."""
    promoted = 0
    for row in rows:
        if row.level != "sentence" or row.score < MIN_TM_SCORE or row.tm_entry_id:
            continue
        existing_tm = session.execute(
            select(TMEntry).where(
                TMEntry.source == row.zh_text,
                TMEntry.source_language == "zh",
                TMEntry.target_language == "en",
            )
        ).scalar_one_or_none()
        if existing_tm:
            continue
        tm = TMEntry(
            source=row.zh_text,
            target=row.en_text,
            source_language="zh",
            target_language="en",
            document_type=document_type,
            domain=domain,
            source_document=zh_doc.title or zh_url,
            url=zh_url,
            authority="official_aligned",
            provenance={
                "aligned_pair_id": row.id,
                "pair_id": pair_id,
                "score": row.score,
            },
        )
        session.add(tm)
        session.flush()
        row.tm_entry_id = tm.id
        promoted += 1
    return promoted
