from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
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


class AlignmentReviewRequest(BaseModel):
    status: str = Field(pattern="^(approved|rejected)$")
    zh_text: str | None = None  # human correction (§34)
    en_text: str | None = None


@router.patch("/alignments/{alignment_id}")
def review_alignment(alignment_id: str, body: AlignmentReviewRequest):
    """Human review of an aligned pair. Approving (or correcting) a pair
    updates translation memory immediately (§34)."""
    from services.retrieval.models import TMEntry

    with SessionLocal() as session:
        pair = session.get(AlignedPair, alignment_id)
        if not pair:
            raise HTTPException(404, "alignment not found")
        if body.zh_text is not None:
            pair.zh_text = body.zh_text
        if body.en_text is not None:
            pair.en_text = body.en_text
        pair.status = body.status
        tm_result = None
        if body.status == "approved":
            tm = session.get(TMEntry, pair.tm_entry_id) if pair.tm_entry_id else None
            if tm is None:
                tm = session.execute(
                    select(TMEntry).where(TMEntry.source == pair.zh_text)
                ).scalar_one_or_none()
            if tm is None:
                tm = TMEntry(
                    source=pair.zh_text, target=pair.en_text,
                    authority="official_verified",  # human-reviewed
                    provenance={"aligned_pair_id": pair.id, "pair_id": pair.pair_id,
                                "reviewed": True},
                )
                session.add(tm)
                session.flush()
                pair.tm_entry_id = tm.id
            else:
                tm.source, tm.target = pair.zh_text, pair.en_text
                tm.authority = "official_verified"
            tm_result = tm.id
        elif pair.tm_entry_id:
            tm = session.get(TMEntry, pair.tm_entry_id)
            if tm:
                session.delete(tm)
            pair.tm_entry_id = None
        session.commit()
        return {"status": pair.status, "tm_entry_id": tm_result if body.status == "approved" else None}


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
