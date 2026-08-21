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
from services.corpus.dedup import PairDeduplicator, content_hash
from services.corpus.models import AlignedPair, CorpusDocument, DocumentPair
from services.corpus.parser import blocks_to_text, extract_metadata, parse_html, split_sentences
from services.retrieval.models import TMEntry

logger = logging.getLogger("govtrans.corpus.ingest")

MIN_TM_SCORE = 0.5  # pairs below this stay in corpus but are not promoted


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


def _collect_pairs(zh_doc, en_doc) -> PairDeduplicator:
    """Paragraph alignment, then sentence alignment within each aligned
    paragraph; everything flows through the deduplicator."""
    dedup = PairDeduplicator()
    zh_paras = [b["text"] for b in zh_doc.structure
                if b["kind"] in ("paragraph", "heading", "list_item")]
    en_paras = [b["text"] for b in en_doc.structure
                if b["kind"] in ("paragraph", "heading", "list_item")]
    for pa in align_sequences(zh_paras, en_paras, allow_merges=False):
        p_idx = pa.zh_idx[0]
        zh_p, en_p = zh_paras[p_idx], en_paras[pa.en_idx[0]]
        zh_sents = split_sentences(zh_p, "zh")
        en_sents = split_sentences(en_p, "en")
        if len(zh_sents) == 1 and len(en_sents) == 1:
            # single-sentence paragraph: sentence level is the same text pair;
            # keep only the TM-ready granularity
            dedup.add(zh_p, en_p, pa.score, level="sentence", idx=f"{p_idx}.0")
            continue
        dedup.add(zh_p, en_p, pa.score, level="paragraph", idx=str(p_idx))
        for sa in align_sequences(zh_sents, en_sents, allow_merges=True):
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
    promote: bool = True,
) -> IngestResult:
    with SessionLocal() as session:
        zh_doc = _store_document(session, raw=zh_source, is_html=is_html, lang="zh",
                                 url=zh_url, document_type=document_type, domain=domain)
        en_doc = _store_document(session, raw=en_source, is_html=is_html, lang="en",
                                 url=en_url, document_type=document_type, domain=domain)
        pair = DocumentPair(zh_doc_id=zh_doc.id, en_doc_id=en_doc.id,
                            match_method=match_method, match_confidence=1.0)
        session.add(pair)
        session.flush()

        dedup = _collect_pairs(zh_doc, en_doc)
        result = IngestResult(zh_doc_id=zh_doc.id, en_doc_id=en_doc.id, pair_id=pair.id,
                              dedup_dropped=dedup.dropped)
        provenance = {"zh_doc": zh_doc.id, "en_doc": en_doc.id,
                      "zh_url": zh_url, "en_url": en_url}
        rows: list[AlignedPair] = []
        for zh_text, en_text, score, meta in dedup.kept:
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
            for row in rows:
                if row.level != "sentence" or row.score < MIN_TM_SCORE:
                    continue
                existing_tm = session.execute(
                    select(TMEntry).where(TMEntry.source == row.zh_text)
                ).scalar_one_or_none()
                if existing_tm:
                    continue
                tm = TMEntry(
                    source=row.zh_text, target=row.en_text,
                    document_type=document_type, domain=domain,
                    source_document=zh_doc.title or zh_url,
                    url=zh_url, authority="official_aligned",
                    provenance={"aligned_pair_id": row.id, "pair_id": pair.id,
                                "score": row.score},
                )
                session.add(tm)
                session.flush()
                row.tm_entry_id = tm.id
                result.promoted_to_tm += 1
        session.commit()
    logger.info("ingested pair %s: %d paragraphs, %d sentences, %d promoted, %d dedup-dropped",
                result.pair_id, result.paragraph_pairs, result.sentence_pairs,
                result.promoted_to_tm, result.dedup_dropped)
    return result
