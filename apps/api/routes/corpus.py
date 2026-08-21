from fastapi import APIRouter, HTTPException
from sqlalchemy import select

from apps.api.db import SessionLocal
from services.corpus.models import AlignedPair, CorpusDocument, DocumentPair

router = APIRouter(prefix="/api/corpus", tags=["corpus"])


@router.get("/documents")
def list_documents(limit: int = 50):
    with SessionLocal() as session:
        rows = session.execute(
            select(CorpusDocument).order_by(CorpusDocument.fetched_at.desc()).limit(limit)
        ).scalars().all()
        return {"documents": [
            {"id": d.id, "url": d.url, "title": d.title, "lang": d.lang,
             "document_type": d.document_type, "domain": d.domain,
             "metadata": d.doc_metadata, "fetched_at": d.fetched_at.isoformat()}
            for d in rows
        ]}


@router.get("/pairs")
def list_pairs(limit: int = 50):
    with SessionLocal() as session:
        rows = session.execute(
            select(DocumentPair).order_by(DocumentPair.created_at.desc()).limit(limit)
        ).scalars().all()
        return {"pairs": [
            {"id": p.id, "zh_doc_id": p.zh_doc_id, "en_doc_id": p.en_doc_id,
             "match_method": p.match_method, "match_confidence": p.match_confidence,
             "status": p.status}
            for p in rows
        ]}


@router.get("/pairs/{pair_id}/alignments")
def list_alignments(pair_id: str, level: str | None = None):
    with SessionLocal() as session:
        stmt = select(AlignedPair).where(AlignedPair.pair_id == pair_id)
        if level:
            stmt = stmt.where(AlignedPair.level == level)
        rows = session.execute(stmt).scalars().all()
        if not rows and not session.get(DocumentPair, pair_id):
            raise HTTPException(404, "pair not found")
        return {"alignments": [
            {"id": a.id, "level": a.level, "idx": a.idx, "zh_text": a.zh_text,
             "en_text": a.en_text, "score": a.score, "status": a.status,
             "tm_entry_id": a.tm_entry_id}
            for a in rows
        ]}
