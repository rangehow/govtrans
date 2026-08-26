import asyncio
import re
from dataclasses import asdict
from datetime import datetime, timezone
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from apps.api.db import SessionLocal
from services.corpus.models import AlignedPair, CorpusDocument, DocumentPair

router = APIRouter(prefix="/api/corpus", tags=["corpus"])


class ScioImportRequest(BaseModel):
    zh_url: str = Field(min_length=12, max_length=2_000)
    en_url: str = Field(min_length=12, max_length=2_000)
    domain: str | None = Field(default=None, max_length=100)
    # GovTrans normally completes the Chinese site's JavaScript challenge in
    # Chromium. Browser-saved official HTML remains an auditable emergency
    # fallback and stays tied to the validated source URL.
    zh_html: str | None = Field(default=None, max_length=2_500_000)
    en_html: str | None = Field(default=None, max_length=2_500_000)


class ScioSyncRequest(BaseModel):
    limit: int = Field(ge=1, le=20)
    domain: str | None = Field(default=None, max_length=100)


class ScioSyncJobRequest(BaseModel):
    years: int = Field(default=10, ge=1, le=20)
    domain: str | None = Field(default="government_white_paper", max_length=100)


def _validate_scio_url(url: str, *, lang: str) -> str:
    parsed = urlparse(url.strip())
    host = (parsed.hostname or "").casefold()
    path = parsed.path.casefold()
    if parsed.scheme not in {"http", "https"}:
        raise HTTPException(422, "SCIO 地址必须使用 http 或 https")
    if lang == "zh":
        valid = (
            host in {"scio.gov.cn", "www.scio.gov.cn"}
            and path.startswith("/zfbps/")
            and path.rstrip("/") != "/zfbps"
            and path.endswith((".htm", ".html"))
        )
    else:
        is_document_hub = bool(re.fullmatch(r"/node_\d+\.html?", path))
        is_known_index = path == "/whitepapers/node_7247532.html"
        valid = (
            host == "english.scio.gov.cn"
            and path.endswith((".htm", ".html"))
            and ("content_" in path or is_document_hub)
            and not is_known_index
        )
    if not valid:
        expected = "www.scio.gov.cn/zfbps/" if lang == "zh" else "english.scio.gov.cn"
        raise HTTPException(422, f"请提交 {expected} 下的官方正文地址，不能使用目录页")
    return url.strip()


@router.post("/scio/import-pair", status_code=201)
async def import_scio_pair(body: ScioImportRequest):
    """Fetch one known bilingual SCIO pair, align it, then distill style.

    Alignment remains evidence-only; it is never silently promoted into the
    runtime terminology store or translation memory.
    """
    from pipelines.style_distillation.mine import mine_candidate_rules
    from services.corpus.crawler import (
        CrawlError,
        FetchedDocument,
        fetch_scio_document,
        validate_document_content,
    )
    from services.corpus.ingest import ingest_document_pair

    zh_url = _validate_scio_url(body.zh_url, lang="zh")
    en_url = _validate_scio_url(body.en_url, lang="en")

    def resolve_source(url: str, supplied_html: str | None) -> FetchedDocument:
        if supplied_html:
            validate_document_content(supplied_html, url)
            return FetchedDocument(supplied_html, (url,))
        return fetch_scio_document(url)

    try:
        zh_fetched, en_fetched = await asyncio.gather(
            asyncio.to_thread(resolve_source, zh_url, body.zh_html),
            asyncio.to_thread(resolve_source, en_url, body.en_html),
        )
        result = await asyncio.to_thread(
            ingest_document_pair,
            zh_source=zh_fetched.html,
            en_source=en_fetched.html,
            is_html=True,
            zh_url=zh_url,
            en_url=en_url,
            document_type="white_paper",
            domain=body.domain,
            match_method=(
                "official_scio_saved"
                if body.zh_html or body.en_html
                else "official_scio_fetch"
            ),
            promote=False,
        )
        mining = await asyncio.to_thread(
            mine_candidate_rules, min_support=2, official_only=True
        )
    except CrawlError as exc:
        raise HTTPException(502, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(500, f"语料导入失败: {type(exc).__name__}: {exc}") from exc
    return {
        "ingest": asdict(result),
        "distillation": mining,
        "source_pages": {
            "zh": list(zh_fetched.page_urls),
            "en": list(en_fetched.page_urls),
        },
    }


@router.post("/scio/sync")
async def sync_scio_catalog(body: ScioSyncRequest):
    """Discover, fetch and ingest recent official bilingual white papers.

    Pairing comes from SCIO's own bilingual hubs. Chinese documents are read
    from ``www.scio.gov.cn/zfbps/`` after the isolated browser completes the
    site's JavaScript challenge; failures are isolated per document so one
    temporarily unavailable page cannot discard successful evidence.
    """
    from pipelines.style_distillation.mine import mine_candidate_rules
    from services.corpus.crawler import CrawlError, discover_scio_pairs, fetch_scio_document
    from services.corpus.ingest import ingest_document_pair

    try:
        candidates = await asyncio.to_thread(discover_scio_pairs, limit=body.limit)
    except CrawlError as exc:
        raise HTTPException(502, str(exc)) from exc

    synced: list[dict] = []
    failed: list[dict] = []
    for candidate in candidates:
        try:
            zh_fetched, en_fetched = await asyncio.gather(
                asyncio.to_thread(fetch_scio_document, candidate.zh_url),
                asyncio.to_thread(fetch_scio_document, candidate.en_url),
            )
            result = await asyncio.to_thread(
                ingest_document_pair,
                zh_source=zh_fetched.html,
                en_source=en_fetched.html,
                is_html=True,
                zh_url=candidate.zh_url,
                en_url=candidate.en_url,
                document_type="white_paper",
                domain=body.domain,
                match_method="official_scio_catalog",
                promote=False,
            )
            synced.append({
                "title": candidate.title,
                "zh_url": candidate.zh_url,
                "en_url": candidate.en_url,
                "pair_id": result.pair_id,
                "sentence_pairs": result.sentence_pairs,
                "reused": bool(result.warnings),
                "source_pages": {
                    "zh": list(zh_fetched.page_urls),
                    "en": list(en_fetched.page_urls),
                },
            })
        except CrawlError as exc:
            failed.append({"title": candidate.title, "error": str(exc)})
        except Exception as exc:
            failed.append({
                "title": candidate.title,
                "error": f"ingest failed ({type(exc).__name__})",
            })

    if not synced:
        detail = failed[0]["error"] if failed else "no document could be synchronized"
        raise HTTPException(502, f"SCIO 自动同步失败: {detail}")
    mining = await asyncio.to_thread(
        mine_candidate_rules,
        min_support=2,
        official_only=True,
    )
    return {
        "discovered": len(candidates),
        "synced": synced,
        "failed": failed,
        "distillation": mining,
    }


@router.post("/scio/sync-jobs", status_code=202)
async def start_scio_sync_job(body: ScioSyncJobRequest, request: Request):
    """Start a resumable rolling-year synchronization outside the request."""
    from services.corpus.sync import serialize_sync_job

    through_year = datetime.now(timezone.utc).year
    since_year = through_year - body.years + 1
    manager = request.app.state.scio_sync
    job, created = manager.create_job(
        since_year=since_year,
        through_year=through_year,
        domain=body.domain,
    )
    manager.start(job.id)
    return {**serialize_sync_job(job), "created": created}


@router.get("/scio/sync-jobs/latest")
def get_latest_scio_sync_job(request: Request):
    from services.corpus.sync import serialize_sync_job

    job = request.app.state.scio_sync.latest_job()
    return {"job": serialize_sync_job(job) if job else None}


@router.get("/scio/sync-jobs/{job_id}")
def get_scio_sync_job(job_id: str, request: Request):
    from services.corpus.sync import serialize_sync_job

    job = request.app.state.scio_sync.get_job(job_id)
    if not job:
        raise HTTPException(404, "corpus sync job not found")
    return serialize_sync_job(job)


@router.get("/documents")
def list_documents(limit: int = Query(default=200, ge=1, le=500)):
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
def list_pairs(limit: int = Query(default=200, ge=1, le=500)):
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
    status: str = Field(pattern="^(auto|approved|rejected)$")
    zh_text: str | None = None  # human correction (§34)
    en_text: str | None = None


@router.patch("/alignments/{alignment_id}")
def review_alignment(alignment_id: str, body: AlignmentReviewRequest):
    """Apply an exceptional human decision to automatically managed evidence.

    High-confidence official alignments need no call to this endpoint. A human
    uses it only to correct, exclude, or explicitly trust a low-score pair.
    """
    from services.retrieval.models import TMEntry

    with SessionLocal() as session:
        pair = session.get(AlignedPair, alignment_id)
        if not pair:
            raise HTTPException(404, "alignment not found")
        document_pair = session.get(DocumentPair, pair.pair_id)
        zh_document = (
            session.get(CorpusDocument, document_pair.zh_doc_id)
            if document_pair else None
        )
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
                    select(TMEntry).where(
                        TMEntry.source == pair.zh_text,
                        TMEntry.source_language == "zh",
                        TMEntry.target_language == "en",
                    )
                ).scalar_one_or_none()
            if tm is None:
                tm = TMEntry(
                    source=pair.zh_text, target=pair.en_text,
                    source_language="zh", target_language="en",
                    document_type=zh_document.document_type if zh_document else None,
                    domain=zh_document.domain if zh_document else None,
                    source_document=(
                        (zh_document.title or zh_document.url) if zh_document else None
                    ),
                    url=zh_document.url if zh_document else None,
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
                if zh_document:
                    tm.document_type = tm.document_type or zh_document.document_type
                    tm.domain = tm.domain or zh_document.domain
                    tm.source_document = (
                        tm.source_document or zh_document.title or zh_document.url
                    )
                    tm.url = tm.url or zh_document.url
            tm_result = tm.id
        elif pair.tm_entry_id:
            tm = session.get(TMEntry, pair.tm_entry_id)
            if tm:
                session.delete(tm)
            pair.tm_entry_id = None
        session.commit()
        from services.retrieval.tm import invalidate_reference_indexes

        invalidate_reference_indexes()
        return {
            "status": pair.status,
            "tm_entry_id": tm_result if body.status == "approved" else None,
        }


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
             "tm_entry_id": a.tm_entry_id,
             "reference_tier": (
                 "excluded" if a.status == "rejected"
                 else "human_verified" if a.status == "approved"
                 else "automatic" if a.score >= 0.85
                 else "archive_only"
             )}
            for a in rows
        ]}
