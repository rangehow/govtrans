"""Persistent, resumable SCIO corpus synchronization.

A ten-year official corpus is too large for one reverse-proxy request. This
manager records progress after every document, runs the work outside the HTTP
request, and resumes active jobs when the API process restarts.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from threading import Lock

from sqlalchemy import or_, select

from apps.api.db import SessionLocal
from services.corpus.models import AlignedPair, CorpusSyncJob, DocumentPair

logger = logging.getLogger("govtrans.corpus.sync")

ACTIVE_SYNC_STATUSES = {"queued", "discovering", "running", "distilling"}
TERMINAL_SYNC_STATUSES = {"completed", "partial", "failed"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


def serialize_sync_job(job: CorpusSyncJob) -> dict:
    result = dict(job.result or {})
    if job.status in TERMINAL_SYNC_STATUSES:
        progress = 1.0
    elif job.discovered > 0:
        progress = min(0.96, 0.05 + 0.9 * job.processed / job.discovered)
    elif job.status == "discovering":
        progress = 0.03
    else:
        progress = 0.0
    return {
        "job_id": job.id,
        "source": job.source,
        "status": job.status,
        "stage": job.stage,
        "since_year": job.since_year,
        "through_year": job.through_year,
        "discovered": job.discovered,
        "processed": job.processed,
        "succeeded": job.succeeded,
        "failed_count": job.failed_count,
        "sentence_pairs": job.sentence_pairs,
        "current_title": job.current_title,
        "progress": progress,
        "error": job.error,
        "synced": result.get("synced", []),
        "failed": result.get("failed", []),
        "distillation": result.get("distillation", {}),
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "updated_at": job.updated_at.isoformat() if job.updated_at else None,
    }


def _remove_unreviewed_pair(pair_id: str) -> bool:
    """Remove stale derived alignment only when no reviewed/TM row depends on it."""
    with SessionLocal() as session:
        pair = session.get(DocumentPair, pair_id)
        if not pair:
            return True
        protected = session.execute(
            select(AlignedPair.id)
            .where(
                AlignedPair.pair_id == pair_id,
                or_(AlignedPair.status != "auto", AlignedPair.tm_entry_id.is_not(None)),
            )
            .limit(1)
        ).scalar_one_or_none()
        if protected:
            return False
        session.query(AlignedPair).filter(AlignedPair.pair_id == pair_id).delete(
            synchronize_session=False
        )
        session.delete(pair)
        session.commit()
        return True


class ScioSyncManager:
    def __init__(self) -> None:
        self._tasks: dict[str, asyncio.Task] = {}
        self._create_lock = Lock()

    def create_job(
        self, *, since_year: int, through_year: int, domain: str | None = None
    ) -> tuple[CorpusSyncJob, bool]:
        """Create one job, or return the active SCIO job to avoid duplicates.

        A completed job with the same range seeds the next run. Its exact URL
        pairs are reused while catalog additions or corrected canonical URLs
        are fetched and aligned, making the button an incremental refresh
        instead of a full decade download every time.
        """
        with self._create_lock, SessionLocal() as session:
            active = session.execute(
                select(CorpusSyncJob)
                .where(
                    CorpusSyncJob.source == "scio",
                    CorpusSyncJob.status.in_(ACTIVE_SYNC_STATUSES),
                )
                .order_by(CorpusSyncJob.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            if active:
                return active, False

            baseline_query = select(CorpusSyncJob).where(
                CorpusSyncJob.source == "scio",
                CorpusSyncJob.status.in_(TERMINAL_SYNC_STATUSES),
                CorpusSyncJob.since_year == since_year,
                CorpusSyncJob.through_year == through_year,
                CorpusSyncJob.succeeded > 0,
            )
            if domain is not None:
                baseline_query = baseline_query.where(CorpusSyncJob.domain == domain)
            baseline = session.execute(
                baseline_query
                .order_by(CorpusSyncJob.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()
            baseline_result = dict(baseline.result or {}) if baseline else {}
            synced = list(baseline_result.get("synced", []))
            sentence_pairs = sum(
                int(item.get("sentence_pairs") or 0) for item in synced
            )
            job = CorpusSyncJob(
                source="scio",
                status="queued",
                stage="catalog",
                since_year=since_year,
                through_year=through_year,
                domain=domain if domain is not None else (baseline.domain if baseline else None),
                discovered=baseline.discovered if baseline else 0,
                processed=len(synced),
                succeeded=len(synced),
                sentence_pairs=sentence_pairs,
                result={
                    "synced": synced,
                    "failed": [],
                    "distillation": baseline_result.get("distillation", {}),
                },
            )
            session.add(job)
            session.commit()
            session.refresh(job)
            return job, True

    def start(self, job_id: str) -> None:
        existing = self._tasks.get(job_id)
        if existing and not existing.done():
            return
        task = asyncio.create_task(self._run_job(job_id), name=f"scio-sync-{job_id}")
        self._tasks[job_id] = task

        def forget(completed: asyncio.Task) -> None:
            if self._tasks.get(job_id) is completed:
                self._tasks.pop(job_id, None)

        task.add_done_callback(forget)

    def resume_active_jobs(self) -> int:
        with SessionLocal() as session:
            rows = session.execute(
                select(CorpusSyncJob).where(
                    CorpusSyncJob.status.in_(ACTIVE_SYNC_STATUSES)
                )
            ).scalars().all()
            for job in rows:
                job.status = "queued"
                job.stage = "catalog"
                job.current_title = None
                job.updated_at = _now()
            session.commit()
            ids = [job.id for job in rows]
        for job_id in ids:
            self.start(job_id)
        return len(ids)

    def get_job(self, job_id: str) -> CorpusSyncJob | None:
        with SessionLocal() as session:
            return session.get(CorpusSyncJob, job_id)

    def latest_job(self) -> CorpusSyncJob | None:
        with SessionLocal() as session:
            return session.execute(
                select(CorpusSyncJob)
                .where(CorpusSyncJob.source == "scio")
                .order_by(CorpusSyncJob.created_at.desc())
                .limit(1)
            ).scalar_one_or_none()

    def _update(self, job_id: str, **values) -> None:
        with SessionLocal() as session:
            job = session.get(CorpusSyncJob, job_id)
            if not job:
                return
            for key, value in values.items():
                setattr(job, key, value)
            job.updated_at = _now()
            session.commit()

    async def _run_job(self, job_id: str) -> None:
        from pipelines.style_distillation.mine import mine_candidate_rules
        from services.corpus.crawler import (
            CrawlError,
            discover_scio_pairs,
            fetch_scio_document,
        )
        from services.corpus.ingest import ingest_document_pair

        with SessionLocal() as session:
            job = session.get(CorpusSyncJob, job_id)
            if not job:
                return
            since_year = job.since_year
            through_year = job.through_year
            domain = job.domain
            persisted = dict(job.result or {})
            # Resume older/partially-created jobs from the most recent useful
            # range baseline as well. This also bridges historical SCIO jobs
            # created before ``government_white_paper`` became the default.
            if not persisted.get("synced"):
                baseline_query = select(CorpusSyncJob).where(
                    CorpusSyncJob.id != job.id,
                    CorpusSyncJob.source == "scio",
                    CorpusSyncJob.status.in_(TERMINAL_SYNC_STATUSES),
                    CorpusSyncJob.since_year == since_year,
                    CorpusSyncJob.through_year == through_year,
                    CorpusSyncJob.succeeded > 0,
                )
                if domain is not None:
                    baseline_query = baseline_query.where(
                        CorpusSyncJob.domain == domain
                    )
                baseline = session.execute(
                    baseline_query
                    .order_by(CorpusSyncJob.created_at.desc())
                    .limit(1)
                ).scalar_one_or_none()
                if baseline:
                    persisted = dict(baseline.result or {})
                    if domain is None:
                        domain = baseline.domain
                        job.domain = domain
                        session.commit()

        synced_items = list(persisted.get("synced", []))
        completed_pairs = {
            (str(item.get("zh_url")), str(item.get("en_url")))
            for item in synced_items
            if item.get("zh_url") and item.get("en_url")
        }
        failed_items: list[dict] = []
        sentence_pairs = sum(int(item.get("sentence_pairs") or 0) for item in synced_items)

        try:
            self._update(
                job_id,
                status="discovering",
                stage="catalog",
                current_title=None,
                error=None,
                processed=len(synced_items),
                succeeded=len(synced_items),
                failed_count=0,
                sentence_pairs=sentence_pairs,
                result={
                    "synced": synced_items,
                    "failed": [],
                    "distillation": persisted.get("distillation", {}),
                },
            )
            candidates = await asyncio.to_thread(
                discover_scio_pairs,
                limit=None,
                since_year=since_year,
                through_year=through_year,
            )
            self._update(
                job_id,
                status="running",
                stage="documents",
                discovered=len(candidates),
                processed=len(synced_items),
            )

            for candidate in candidates:
                candidate_key = (candidate.zh_url, candidate.en_url)
                if candidate_key in completed_pairs:
                    continue
                self._update(job_id, current_title=candidate.title)
                try:
                    zh_fetched, en_fetched = await asyncio.gather(
                        asyncio.to_thread(fetch_scio_document, candidate.zh_url),
                        asyncio.to_thread(fetch_scio_document, candidate.en_url),
                    )
                    ingest = await asyncio.to_thread(
                        ingest_document_pair,
                        zh_source=zh_fetched.html,
                        en_source=en_fetched.html,
                        is_html=True,
                        zh_url=candidate.zh_url,
                        en_url=candidate.en_url,
                        document_type="white_paper",
                        domain=domain,
                        match_method="official_scio_catalog",
                        promote=False,
                    )
                    item = {
                        "title": candidate.title,
                        "publish_year": candidate.publish_year,
                        "zh_url": candidate.zh_url,
                        "en_url": candidate.en_url,
                        "pair_id": ingest.pair_id,
                        "sentence_pairs": ingest.sentence_pairs,
                        "reused": bool(ingest.warnings),
                    }
                    superseded = [
                        previous
                        for previous in synced_items
                        if previous.get("zh_url") == candidate.zh_url
                        and previous.get("en_url") != candidate.en_url
                    ]
                    for previous in superseded:
                        old_pair_id = str(previous.get("pair_id") or "")
                        if old_pair_id and old_pair_id != ingest.pair_id:
                            _remove_unreviewed_pair(old_pair_id)
                        sentence_pairs -= int(previous.get("sentence_pairs") or 0)
                        synced_items.remove(previous)
                        completed_pairs.discard((
                            str(previous.get("zh_url")),
                            str(previous.get("en_url")),
                        ))
                    synced_items.append(item)
                    completed_pairs.add(candidate_key)
                    sentence_pairs += ingest.sentence_pairs
                except CrawlError as exc:
                    failed_items.append({
                        "title": candidate.title,
                        "publish_year": candidate.publish_year,
                        "error": str(exc),
                    })
                except Exception as exc:
                    logger.exception("SCIO corpus ingest failed for %s", candidate.title)
                    failed_items.append({
                        "title": candidate.title,
                        "publish_year": candidate.publish_year,
                        "error": f"ingest failed ({type(exc).__name__})",
                    })

                processed = len(synced_items) + len(failed_items)
                self._update(
                    job_id,
                    processed=processed,
                    succeeded=len(synced_items),
                    failed_count=len(failed_items),
                    sentence_pairs=sentence_pairs,
                    result={
                        "synced": synced_items,
                        "failed": failed_items[-100:],
                        "distillation": persisted.get("distillation", {}),
                    },
                )

            if not synced_items:
                detail = failed_items[0]["error"] if failed_items else "no bilingual document"
                self._update(
                    job_id,
                    status="failed",
                    stage="complete",
                    current_title=None,
                    error=f"SCIO 自动同步失败: {detail}",
                )
                return

            self._update(
                job_id,
                status="distilling",
                stage="distillation",
                current_title=None,
            )
            mining = await asyncio.to_thread(
                mine_candidate_rules,
                min_support=2,
                official_only=True,
            )
            final_result = {
                "synced": synced_items,
                "failed": failed_items[-100:],
                "distillation": mining,
            }
            self._update(
                job_id,
                status="partial" if failed_items else "completed",
                stage="complete",
                processed=len(synced_items) + len(failed_items),
                succeeded=len(synced_items),
                failed_count=len(failed_items),
                sentence_pairs=sentence_pairs,
                current_title=None,
                result=final_result,
                error=(f"{len(failed_items)} 份文档暂时失败，可自动重试" if failed_items else None),
            )
        except asyncio.CancelledError:
            self._update(
                job_id,
                status="queued",
                stage="catalog",
                current_title=None,
            )
            raise
        except Exception as exc:
            logger.exception("SCIO synchronization job failed")
            self._update(
                job_id,
                status="partial" if synced_items else "failed",
                stage="complete",
                current_title=None,
                error=f"同步任务异常（{type(exc).__name__}），可重新启动后续传",
                result={
                    "synced": synced_items,
                    "failed": failed_items[-100:],
                    "distillation": persisted.get("distillation", {}),
                },
            )

    async def shutdown(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
