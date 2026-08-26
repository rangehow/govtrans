"""Translation Orchestrator — executes the deterministic stage graph.

Guarantees:
- idempotent stages: re-running a completed stage is a no-op, so a run
  resumes after server restart from run.current_stage (§8);
- every stage emits persisted SSE events (started/progress/completed/failed);
- cancellation is honored between stages and between segments;
- autonomous release gate: blocking issues after final_qa loop back to an
  escalated finalizer, then pause fail-closed with a bounded continuation path.
"""

from __future__ import annotations

import asyncio
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from apps.api.config import Settings
from apps.api.db import SessionLocal
from agents.roles import llm
from services.languages import (
    pair_capabilities,
    prompt_language_variables,
    resolve_language_pair,
)
from services.orchestrator.events import RunEventOut
from services.orchestrator.models import (
    DocumentGlossary,
    Issue,
    RunEvent,
    RunStatus,
    Segment,
    TranslationRun,
)
from services.orchestrator.skills import (
    base_skills_for,
    render_style_contract,
    resolve_style_skills,
    skill_version,
)
from services.orchestrator.segmentation import split_source_text
from services.orchestrator.stage_graph import (
    STAGES,
    STAGE_INDEX,
    Stage,
    stage_runtime_spec,
)
from services.orchestrator.tofu_client import TofuClient, TofuError
from services.quality import validators
from services.retrieval.search import LeakGuardError, QueryLeakGuard, official_search
from services.terminology import service as term_service

logger = logging.getLogger("govtrans.engine")

REVIEW_DIMENSIONS = {
    "semantic_review": (
        "semantic",
        "Faithfulness to the source meaning: omissions, additions, distortions, "
        "mistranslated policy concepts, wrong logical relations.",
    ),
    "style_review": (
        "style",
        "Natural official register in the target language: formal, declarative, "
        "concise, genre-appropriate, and free of colloquialisms.",
    ),
}


def _utcnow_iso() -> datetime:
    return datetime.now(timezone.utc)


def _evidence_supports_target(evidence: list[dict], target: str) -> bool:
    """A search hit is proof only when an official page contains the rendering."""
    needle = " ".join(target.casefold().split())
    if not needle:
        return False
    return any(
        item.get("authority") == "official_web"
        and needle
        in " ".join(f"{item.get('title', '')} {item.get('snippet', '')}".casefold().split())
        for item in evidence
    )


def _binding_glossary(entries: list[dict]) -> list[dict]:
    """Return only release-binding terms, including safe legacy rows."""
    binding: list[dict] = []
    for entry in entries:
        mandatory = entry.get("mandatory")
        if mandatory is None:
            mandatory = "origin" not in entry or entry.get("origin") in {
                "term_db",
                "official_verified",
            }
        if mandatory and not entry.get("exception"):
            binding.append(entry)
    return binding


def _normalize_common_term_case(target: str) -> str:
    """Store non-proper LLM suggestions in dictionary-style sentence case."""
    token_pattern = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z][A-Za-z0-9]*)*")

    def normalize(match: re.Match[str]) -> str:
        token = match.group(0)
        return token if token.isupper() and len(token) > 1 else token.lower()

    return token_pattern.sub(normalize, target)


def _normalize_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Do not let a model downgrade objective publication defects to a nit."""
    normalized = dict(finding)
    searchable = f"{normalized.get('category', '')} {normalized.get('message', '')}".casefold()
    capitalization_markers = (
        "capitaliz",
        "title case",
        "uppercase",
        "lowercase",
        "大小写",
        "大写",
        "小写",
    )
    if normalized.get("severity") == "minor" and any(
        marker in searchable for marker in capitalization_markers
    ):
        normalized["severity"] = "major"
    return normalized


def _build_contiguous_batches(
    segments: list[dict[str, Any]], max_chars: int
) -> list[list[dict[str, Any]]]:
    """Pack ordered persistence units into model calls without reordering.

    A normal document that fits the budget is therefore translated in one
    call. Paragraph boundaries remain available for storage and issue mapping.
    """
    batches: list[list[dict[str, Any]]] = []
    current: list[dict[str, Any]] = []
    current_chars = 0
    for segment in segments:
        size = len(segment.get("source", "")) + len(segment.get("translation") or "") + 80
        if current and current_chars + size > max_chars:
            batches.append(current)
            current = []
            current_chars = 0
        current.append(segment)
        current_chars += size
    if current:
        batches.append(current)
    return batches


def _context_window(
    segments: list[dict[str, Any]], batch: list[dict[str, Any]], max_chars: int
) -> list[dict[str, Any]]:
    """Return the full document when it fits, otherwise an adjacent window."""

    def size(item: dict[str, Any]) -> int:
        return len(item.get("source", "")) + len(item.get("translation") or "") + 80

    total = sum(size(item) for item in segments)
    if total <= max_chars:
        return segments
    positions = {item["idx"] for item in batch}
    left, right = min(positions), max(positions)
    used = sum(size(item) for item in segments[left : right + 1])
    while used < max_chars and (left > 0 or right + 1 < len(segments)):
        candidates: list[tuple[str, int, int]] = []
        if left > 0:
            candidates.append(("left", left - 1, size(segments[left - 1])))
        if right + 1 < len(segments):
            candidates.append(("right", right + 1, size(segments[right + 1])))
        side, index, extra = min(candidates, key=lambda item: item[2])
        if used + extra > max_chars:
            break
        if side == "left":
            left = index
        else:
            right = index
        used += extra
    return segments[left : right + 1]


def _model_segment_id(segment: dict[str, Any]) -> str:
    """Use short stable paragraph labels at the model boundary.

    Long random database UUIDs are needlessly difficult for a model to copy
    and can be spliced together. Paragraph ordinals are deterministic within a
    run and are mapped back to UUIDs before anything is persisted.
    """
    return f"P{int(segment['idx']) + 1}"


def _resolve_model_segment_id(
    value: Any,
    batch: list[dict[str, Any]],
    finding: dict[str, Any] | None = None,
) -> tuple[str | None, str]:
    """Resolve a model paragraph label without letting bad output fail a run.

    Exact short labels are the normal path. Legacy UUIDs remain accepted, and
    a uniquely identifiable malformed UUID/span can be repaired. Ambiguous
    output is deliberately ignored instead of being attached to the wrong
    paragraph or aborting an otherwise usable translation.
    """
    candidate = str(value or "").strip()
    aliases = {_model_segment_id(item): item["id"] for item in batch}
    actual_ids = {item["id"] for item in batch}
    if candidate in aliases:
        return aliases[candidate], "alias"
    if candidate in actual_ids:
        return candidate, "actual"
    if candidate.isdigit():
        ordinal_alias = f"P{int(candidate)}"
        if ordinal_alias in aliases:
            return aliases[ordinal_alias], "ordinal"

    if len(candidate) >= 8:
        prefix_matches: list[tuple[int, str]] = []
        for actual_id in actual_ids:
            common = 0
            for left, right in zip(candidate, actual_id):
                if left != right:
                    break
                common += 1
            if common >= 8:
                prefix_matches.append((common, actual_id))
        if prefix_matches:
            best = max(length for length, _actual_id in prefix_matches)
            best_ids = {actual_id for length, actual_id in prefix_matches if length == best}
            if len(best_ids) == 1:
                return best_ids.pop(), "uuid_prefix"

    finding = finding or {}
    for field, text_key in (("source_span", "source"), ("target_span", "translation")):
        span = " ".join(str(finding.get(field) or "").casefold().split())
        if len(span) < 4:
            continue
        matches = []
        for item in batch:
            text = " ".join(str(item.get(text_key) or "").casefold().split())
            if span in text:
                matches.append(item["id"])
        if len(matches) == 1:
            return matches[0], field
    return None, "unresolved"


class Orchestrator:
    def __init__(self, settings: Settings, tofu: TofuClient) -> None:
        self.settings = settings
        self.tofu = tofu
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}
        self._early_translation_tasks: dict[str, asyncio.Task[None]] = {}
        self._event_locks: dict[str, threading.Lock] = {}
        self._restart_pending: set[str] = set()

    # ------------------------------------------------------------- lifecycle
    def create_run(
        self,
        *,
        source_text: str,
        confidentiality: str,
        document_type: str | None = None,
        direction: str | None = None,
        source_language: str | None = None,
        target_language: str | None = None,
        style_skills: list[str] | None = None,
        manual_terms: list[dict[str, Any]] | None = None,
        translation_mode: str = "coherent",
    ) -> str:
        source_language, target_language = resolve_language_pair(
            source_language, target_language, direction
        )
        direction = f"{source_language}-{target_language}"
        selected_styles = resolve_style_skills(
            style_skills,
            document_type,
            source_language=source_language,
            target_language=target_language,
        )
        if translation_mode not in {"coherent", "balanced"}:
            raise ValueError(f"未知翻译策略: {translation_mode}")
        with SessionLocal() as session:
            run = TranslationRun(
                source_text=source_text,
                confidentiality=confidentiality,
                document_type=document_type,
                direction=direction,
                source_language=source_language,
                target_language=target_language,
                style_skills=selected_styles,
                manual_terms=manual_terms or [],
                translation_mode=translation_mode,
                pipeline_version=self.settings.pipeline_version,
                version_pins=self._version_pins(
                    selected_styles,
                    source_language=source_language,
                    target_language=target_language,
                    style_auto=style_skills is None,
                ),
            )
            session.add(run)
            session.commit()
            run_id = run.id
        self.emit(
            run_id,
            "run.created",
            "run",
            "started",
            "运行已创建",
            f"{direction} · 机密分级 {confidentiality}",
        )
        return run_id

    def _version_pins(
        self,
        selected_styles: list[str],
        *,
        source_language: str,
        target_language: str,
        style_auto: bool = False,
    ) -> dict[str, Any]:
        selected_skills = [
            *base_skills_for(source_language, target_language),
            *selected_styles,
        ]
        return {
            "pipeline_version": self.settings.pipeline_version,
            "translator_model": self.settings.translator_model,
            "review_model": self.settings.review_model,
            "fast_model": self.settings.fast_model,
            "prompt_versions": {
                name: llm.prompt_version(name)
                for name in (
                    "analyze",
                    "term_extract",
                    "translate_batch",
                    "document_review",
                    "coherence_review",
                    "finalize",
                    "finalize_batch",
                )
            },
            "style_auto": style_auto,
            "source_language": source_language,
            "target_language": target_language,
            "pair_capabilities": pair_capabilities(source_language, target_language),
            "skill_versions": {name: skill_version(name) for name in selected_skills},
        }

    def start(self, run_id: str) -> None:
        if run_id in self._tasks and not self._tasks[run_id].done():
            return
        self._tasks[run_id] = asyncio.create_task(self._guarded_execute(run_id))

    def _start_after_current_task(self, run_id: str) -> None:
        """Restart a reopened terminal run after its old executor unwinds."""
        current = self._tasks.get(run_id)
        if current is None or current.done():
            self.start(run_id)
            return
        if run_id in self._restart_pending:
            return
        self._restart_pending.add(run_id)
        loop = current.get_loop()

        def restart(finished: asyncio.Task) -> None:
            self._restart_pending.discard(run_id)
            if self._tasks.get(run_id) is finished:
                self._tasks.pop(run_id, None)
            loop.call_soon(self.start, run_id)

        current.add_done_callback(restart)

    def continue_quality(self, run_id: str) -> bool:
        """Resume a quality pause or retry an interrupted idempotent stage.

        Automatic loops remain bounded, but a user is never left at a dead end:
        the existing source, translations, evidence, and issue history are kept
        while the repair budget is renewed. A technical failure resumes from
        its persisted current stage; a quality pause resumes at finalization.
        """
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if not run or run.status not in {RunStatus.QUALITY_GATE_FAILED, RunStatus.FAILED}:
                return False
            was_failed = run.status == RunStatus.FAILED
            pins = dict(run.version_pins or {})
            pins["previous_revision_rounds"] = int(pins.get("previous_revision_rounds") or 0) + int(
                run.loop_count or 0
            )
            pins["manual_continue_count"] = int(pins.get("manual_continue_count") or 0) + 1
            run.version_pins = pins
            run.loop_count = 0
            resume_stage = (
                run.current_stage
                if was_failed and run.current_stage in STAGE_INDEX
                else "finalize"
            )
            resume_stage = resume_stage or "parse"
            if resume_stage == "complete":
                resume_stage = "final_qa"
            run.current_stage = resume_stage
            run.status = STAGES[STAGE_INDEX[resume_stage]].run_status
            run.error = None
            # Progress represents completed work and therefore never rewinds
            # when a targeted repair cycle revisits an earlier graph node.
            run.progress = max(
                run.progress,
                STAGE_INDEX[resume_stage] / len(STAGES),
            )
            progress = run.progress
            continue_count = pins["manual_continue_count"]
            session.commit()
        self.emit(
            run_id,
            "run.failure_retry" if was_failed else "run.quality_continue",
            resume_stage,
            "started",
            "重试中断步骤" if was_failed else "继续自动优化",
            (
                "已保留全部完成结果，正在从中断步骤继续"
                if was_failed
                else "已保留全部译文与审校依据，正在启动新一轮定向修订"
            ),
            progress=progress,
            metrics={"continue_count": continue_count, "resume_stage": resume_stage},
        )
        self._start_after_current_task(run_id)
        return True

    def resume_active_runs(self) -> int:
        """Called at API startup: server restart must not lose runs (§8)."""
        with SessionLocal() as session:
            ids = [
                r.id
                for r in session.query(TranslationRun)
                .filter(TranslationRun.status.in_(RunStatus.ACTIVE))
                .all()
            ]
        for run_id in ids:
            logger.info("resuming run %s after restart", run_id)
            self.start(run_id)
        return len(ids)

    def cancel(self, run_id: str) -> bool:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if not run or run.status in RunStatus.TERMINAL:
                return False
            run.status = RunStatus.CANCELLED
            session.commit()
        self.emit(run_id, "run.cancelled", "run", "failed", "运行已取消", None)
        return True

    # ---------------------------------------------------------------- events
    def emit(
        self,
        run_id: str,
        type_: str,
        phase: str,
        status: str,
        title: str,
        summary: str | None,
        *,
        progress: float | None = None,
        segment_ids: list[str] | None = None,
        evidence: list[dict] | None = None,
        metrics: dict | None = None,
    ) -> RunEventOut:
        # Segment workers finish concurrently. Serialize the per-run sequence
        # allocator and also lock the parent row on PostgreSQL so event cursors
        # remain strictly monotonic across API workers.
        event_lock = self._event_locks.setdefault(run_id, threading.Lock())
        with event_lock, SessionLocal() as session:
            session.query(TranslationRun).filter_by(id=run_id).with_for_update().first()
            last_seq = (
                session.query(RunEvent.seq)
                .filter(RunEvent.run_id == run_id)
                .order_by(RunEvent.seq.desc())
                .first()
            )
            seq = (last_seq[0] if last_seq else 0) + 1
            row = RunEvent(
                run_id=run_id,
                seq=seq,
                type=type_,
                phase=phase,
                status=status,
                title=title,
                summary=summary,
                progress=progress,
                segment_ids=segment_ids or [],
                evidence=evidence or [],
                metrics=metrics or {},
            )
            session.add(row)
            session.commit()
            event = RunEventOut(
                id=row.id,
                run_id=run_id,
                seq=seq,
                type=type_,
                phase=phase,
                status=status,
                title=title,
                summary=summary,
                progress=progress,
                segment_ids=row.segment_ids,
                evidence=row.evidence,
                metrics=row.metrics,
                created_at=row.created_at or _utcnow_iso(),
            )
        for queue in list(self._subscribers.get(run_id, ())):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # The database is authoritative; a slow subscriber can replay
                # the dropped event by cursor instead of stalling the pipeline.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except (asyncio.QueueEmpty, asyncio.QueueFull):
                    pass
        return event

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        self._subscribers.get(run_id, set()).discard(queue)

    # -------------------------------------------------------------- execution
    async def _guarded_execute(self, run_id: str) -> None:
        while True:
            try:
                await self.execute(run_id)
                return
            except asyncio.CancelledError:
                raise
            except TofuError as exc:
                if exc.kind != "overloaded":
                    self._fail_run(run_id, exc)
                    return
                # ToFu refuses before task creation when either its execution
                # slots or its host-memory safety gate are full. Preserve the
                # current stage and keep the run active: all stage writes are
                # idempotent, so automatic replay is safe after this process
                # sleeps or after an API restart.
                with SessionLocal() as session:
                    run = session.get(TranslationRun, run_id)
                    if not run or run.status in RunStatus.TERMINAL:
                        return
                    phase = run.current_stage or "run"
                    progress = run.progress
                    pins = dict(run.version_pins or {})
                    waits = dict(pins.get("resource_waits") or {})
                    wait_count = int(waits.get(phase, 0)) + 1
                    waits[phase] = wait_count
                    pins["resource_waits"] = waits
                    run.version_pins = pins
                    if wait_count > self.settings.tofu_resource_max_waits:
                        session.commit()
                        self._fail_run(
                            run_id,
                            TofuError(
                                "overloaded",
                                f"推理资源在 {self.settings.tofu_resource_max_waits} 轮自动等待后"
                                "仍未能接收任务，"
                                "已停止无限等待；源文和进度已保存",
                                status=503,
                                retryable=True,
                            ),
                        )
                        return
                    run.status = RunStatus.WAITING_RESOURCES
                    run.error = None
                    session.commit()
                retry_seconds = self.settings.tofu_resource_retry_seconds
                logger.warning(
                    "run %s waiting %.1fs for ToFu admission capacity",
                    run_id,
                    retry_seconds,
                )
                self.emit(
                    run_id,
                    "run.resource_wait",
                    phase,
                    "progress",
                    "等待推理资源",
                    f"任务已持久化，正在进行第 {wait_count}/"
                    f"{self.settings.tofu_resource_max_waits} 轮自动重试",
                    progress=progress,
                    metrics={
                        "retry_after_seconds": retry_seconds,
                        "attempt": wait_count,
                        "max_attempts": self.settings.tofu_resource_max_waits,
                    },
                )
                await asyncio.sleep(retry_seconds)
            except Exception as exc:  # every failure must be observable (§51)
                self._fail_run(run_id, exc)
                return

    def _fail_run(self, run_id: str, exc: Exception) -> None:
        logger.exception("run %s failed", run_id, exc_info=exc)
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if run and run.status not in RunStatus.TERMINAL:
                run.status = RunStatus.FAILED
                run.error = f"{type(exc).__name__}: {exc}"[:2000]
                session.commit()
        self.emit(
            run_id, "run.failed", "run", "failed", "运行失败", f"{type(exc).__name__}: {exc}"[:500]
        )

    def _start_early_translation(self, run_id: str) -> bool:
        """Overlap one short-document translation call with preflight work."""
        if self.settings.early_translation_max_chars <= 0:
            return False
        existing = self._early_translation_tasks.get(run_id)
        if existing and not existing.done():
            return True
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if (
                not run
                or len(run.source_text) > self.settings.early_translation_max_chars
                or bool(run.manual_terms)
                or not run.segments
                or any(segment.status != "pending" for segment in run.segments)
            ):
                return False
            progress = run.progress
        task = asyncio.create_task(self._stage_translate_impl(run_id, early=True))
        self._early_translation_tasks[run_id] = task

        def observe(completed: asyncio.Task[None]) -> None:
            if completed.cancelled():
                return
            error = completed.exception()
            if error:
                logger.info("run=%s early translation will fall back: %s", run_id, error)

        task.add_done_callback(observe)
        self.emit(
            run_id,
            "translate.early_started",
            "translate",
            "started",
            "短文初译已并行启动",
            "译文生成与文档分析、术语研究同步进行",
            progress=progress,
            metrics={
                "kind": "model",
                "roles": ["translator"],
                "models": [self.settings.translator_model],
                "engine": "短文并行翻译",
            },
        )
        return True

    async def _cancel_early_translation(self, run_id: str) -> None:
        task = self._early_translation_tasks.pop(run_id, None)
        if not task:
            return
        if not task.done():
            task.cancel()
        await asyncio.gather(task, return_exceptions=True)

    async def execute(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if not run or run.status in RunStatus.TERMINAL:
                return
            start_id = run.current_stage or STAGES[0].id
        idx = STAGE_INDEX.get(start_id, 0)
        while idx < len(STAGES):
            stage = STAGES[idx]
            with SessionLocal() as session:
                run = session.get(TranslationRun, run_id)
                terminal = run is None or run.status in RunStatus.TERMINAL
            if terminal:
                await self._cancel_early_translation(run_id)
                return
            if self._is_cancelled(run_id):
                return
            parallel_ids: tuple[str, ...] = ()
            if stage.id == "analyze":
                parallel_ids = ("analyze", "terminology")
            elif stage.id == "semantic_review":
                parallel_ids = ("semantic_review", "style_review", "consistency_review")
            parallel_stages = STAGES[idx : idx + len(parallel_ids)]
            if parallel_ids and tuple(item.id for item in parallel_stages) == parallel_ids:
                early_started = stage.id == "analyze" and self._start_early_translation(run_id)
                try:
                    await self._run_stage_group(run_id, parallel_stages, idx)
                except BaseException:
                    if early_started:
                        await self._cancel_early_translation(run_id)
                    raise
                idx += len(parallel_stages)
            else:
                await self._run_stage(run_id, stage, idx)
                idx += 1
            # Release-gate loop: final_qa decided to go back to finalize.
            with SessionLocal() as session:
                run = session.get(TranslationRun, run_id)
                terminal = run is None or run.status in RunStatus.TERMINAL
                next_stage = run.current_stage if run else None
            if terminal:
                await self._cancel_early_translation(run_id)
                return
            if next_stage and STAGE_INDEX.get(next_stage, 0) < idx:
                idx = STAGE_INDEX[next_stage]

    def _is_cancelled(self, run_id: str) -> bool:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
        return run is None or run.status == RunStatus.CANCELLED

    async def _run_with_heartbeats(
        self,
        run_id: str,
        stage: Stage,
        executor: Callable[[str], Any],
        runtime_meta: dict[str, Any],
        initial_progress: float,
    ) -> None:
        """Await one executor while publishing truthful liveness.

        A heartbeat deliberately keeps the persisted percentage unchanged. It
        proves only that the orchestrator is alive and still awaiting this
        stage; it never pretends that opaque model generation is further along
        than the last completed unit of work.
        """
        task = asyncio.create_task(executor(run_id))
        started = asyncio.get_running_loop().time()
        interval = self.settings.run_progress_heartbeat_seconds
        try:
            while True:
                done, _ = await asyncio.wait({task}, timeout=interval)
                if task in done:
                    await task
                    return

                elapsed = max(1, int(asyncio.get_running_loop().time() - started))
                with SessionLocal() as session:
                    run = session.get(TranslationRun, run_id)
                    if not run or run.status in RunStatus.TERMINAL:
                        task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                        return
                    progress = max(initial_progress, run.progress)

                is_model_work = runtime_meta.get("kind") in {"model", "hybrid"}
                self.emit(
                    run_id,
                    f"stage.{stage.id}.heartbeat",
                    stage.id,
                    "progress",
                    f"{stage.title}仍在处理",
                    (
                        f"模型任务仍在等待结果，已持续 {elapsed} 秒；"
                        "完成后会立即推进"
                        if is_model_work
                        else f"后台任务仍在处理，已持续 {elapsed} 秒"
                    ),
                    progress=progress,
                    metrics={
                        **runtime_meta,
                        "heartbeat": True,
                        "elapsed_seconds": elapsed,
                    },
                )
        except BaseException:
            if not task.done():
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            raise

    async def _run_stage_group(self, run_id: str, stages: list[Stage], idx: int) -> None:
        """Run independent adjacent stages concurrently with honest events."""
        progress_base = idx / len(STAGES)
        stage_ids = [stage.id for stage in stages]
        runtimes = {
            stage.id: {
                **stage_runtime_spec(stage.id, self.settings),
                "execution": "parallel",
                "parallel_stages": stage_ids,
            }
            for stage in stages
        }
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if run.status != RunStatus.WAITING_RESOURCES:
                run.status = stages[0].run_status
            run.current_stage = stages[0].id
            run.progress = max(run.progress, progress_base)
            visible_progress = run.progress
            session.commit()
        for stage in stages:
            self.emit(
                run_id,
                f"stage.{stage.id}",
                stage.id,
                "started",
                stage.title,
                "与其他独立步骤并行执行",
                progress=visible_progress,
                metrics=runtimes[stage.id],
            )

        executors = self._executors()
        tasks = [
            asyncio.create_task(
                self._run_with_heartbeats(
                    run_id,
                    stage,
                    executors[stage.id],
                    runtimes[stage.id],
                    visible_progress,
                )
            )
            for stage in stages
        ]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for stage in stages:
                self.emit(
                    run_id,
                    f"stage.{stage.id}",
                    stage.id,
                    "failed",
                    stage.title,
                    "并行步骤未完成",
                    progress=visible_progress,
                    metrics=runtimes[stage.id],
                )
            raise

        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            terminal_status = run.status if run else None
            terminal_progress = run.progress if run else visible_progress
            terminal = run is None or terminal_status in RunStatus.TERMINAL
        if terminal:
            for stage in stages:
                self.emit(
                    run_id,
                    f"stage.{stage.id}",
                    stage.id,
                    "failed",
                    stage.title,
                    "运行在并行步骤中终止",
                    progress=terminal_progress,
                    metrics=runtimes[stage.id],
                )
            return

        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            run.progress = max(run.progress, (idx + len(stages)) / len(STAGES))
            completed_progress = run.progress
            if run.status == RunStatus.WAITING_RESOURCES:
                run.status = stages[-1].run_status
            pins = dict(run.version_pins or {})
            waits = dict(pins.get("resource_waits") or {})
            for stage_id in stage_ids:
                waits.pop(stage_id, None)
            pins["resource_waits"] = waits
            run.version_pins = pins
            if run.current_stage in stage_ids:
                next_index = idx + len(stages)
                run.current_stage = (
                    STAGES[next_index].id if next_index < len(STAGES) else "complete"
                )
            session.commit()
        for stage in stages:
            self.emit(
                run_id,
                f"stage.{stage.id}",
                stage.id,
                "completed",
                stage.title,
                "并行步骤已完成",
                progress=completed_progress,
                metrics=runtimes[stage.id],
            )

    async def _run_stage(self, run_id: str, stage: Stage, idx: int) -> None:
        progress_base = idx / len(STAGES)
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            # A resumed resource-waiting run stays visibly queued while the
            # admission attempt is in progress. Switching it back to a normal
            # stage label before ToFu has accepted work would make the UI
            # oscillate between "analyzing" and "waiting" on every retry.
            if run.status != RunStatus.WAITING_RESOURCES and stage.id != "complete":
                run.status = stage.run_status
            run.current_stage = stage.id
            run.progress = max(run.progress, progress_base)
            visible_progress = run.progress
            runtime_meta = stage_runtime_spec(
                stage.id, self.settings, loop_count=int(run.loop_count or 0)
            )
            session.commit()
        self.emit(
            run_id,
            f"stage.{stage.id}",
            stage.id,
            "started",
            stage.title,
            None,
            progress=visible_progress,
            metrics=runtime_meta,
        )
        executor = self._executors()[stage.id]
        await self._run_with_heartbeats(
            run_id,
            stage,
            executor,
            runtime_meta,
            visible_progress,
        )
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            terminal_status = run.status if run else None
            terminal_progress = run.progress if run else visible_progress
            terminal = run is None or terminal_status in RunStatus.TERMINAL
        completed_normally = stage.id == "complete" and terminal_status == RunStatus.COMPLETED
        if terminal and not completed_normally:
            self.emit(
                run_id,
                f"stage.{stage.id}",
                stage.id,
                "failed",
                stage.title,
                "运行在该阶段终止",
                progress=terminal_progress,
                metrics=runtime_meta,
            )
            return
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            run.progress = max(run.progress, (idx + 1) / len(STAGES))
            completed_progress = run.progress
            if run.status == RunStatus.WAITING_RESOURCES:
                # The executor returned, proving the blocked model call was
                # eventually admitted. Restore the stage status before normal
                # stage advancement resumes.
                run.status = stage.run_status
            pins = dict(run.version_pins or {})
            waits = dict(pins.get("resource_waits") or {})
            if stage.id in waits:
                waits.pop(stage.id, None)
                pins["resource_waits"] = waits
                run.version_pins = pins
            # Executors may redirect the flow (release-gate loop in final_qa
            # points current_stage back at finalize) — only auto-advance when
            # the stage did not redirect.
            if run.current_stage == stage.id:
                run.current_stage = STAGES[idx + 1].id if idx + 1 < len(STAGES) else "complete"
            session.commit()
        self.emit(
            run_id,
            f"stage.{stage.id}",
            stage.id,
            "completed",
            stage.title,
            None,
            progress=completed_progress,
            metrics=runtime_meta,
        )

    def _executors(self) -> dict[str, Callable[[str], Any]]:
        return {
            "parse": self._stage_parse,
            "analyze": self._stage_analyze,
            "terminology": self._stage_terminology,
            "retrieve": self._stage_retrieve,
            "plan": self._stage_plan,
            "translate": self._stage_translate,
            "deterministic_qa": self._stage_deterministic_qa,
            "term_review": self._stage_term_review,
            "semantic_review": self._make_llm_reviewer("semantic_review"),
            "style_review": self._make_llm_reviewer("style_review"),
            "consistency_review": self._stage_consistency_review,
            "finalize": self._stage_finalize,
            "final_qa": self._stage_final_qa,
            "complete": self._stage_complete,
        }

    # ---------------------------------------------------------------- stages
    async def _stage_parse(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if run.segments:  # idempotent
                return
            chunks = split_source_text(
                run.source_text,
                max_chars=self.settings.segment_max_chars,
            )
            if not chunks:
                raise ValueError("源文为空，无法解析")
            for i, chunk in enumerate(chunks):
                session.add(Segment(run_id=run_id, idx=i, source=chunk))
            session.commit()
            count = len(chunks)
        self.emit(
            run_id,
            "parse.segments",
            "parse",
            "progress",
            "段落结构整理完成",
            f"共 {count} 个段落单元",
        )

    async def _stage_analyze(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if run.summary:
                return
            source = run.source_text
            source_language = run.source_language
            target_language = run.target_language
        result = await llm.call_role(
            tofu=self.tofu,
            settings=self.settings,
            role="analyst",
            prompt_name="analyze",
            variables={
                "source_text": source,
                **prompt_language_variables(source_language, target_language),
            },
            schema_name="analyze",
            model=self.settings.fast_model,
            run_id=run_id,
        )
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            run.summary = result["summary"]
            run.document_type = run.document_type or result["document_type"]
            pins = dict(run.version_pins)
            pins["analysis"] = result
            if pins.get("style_auto"):
                run.style_skills = resolve_style_skills(
                    None,
                    run.document_type,
                    source_language=run.source_language,
                    target_language=run.target_language,
                )
                pins["skill_versions"] = {
                    name: skill_version(name)
                    for name in [
                        *base_skills_for(run.source_language, run.target_language),
                        *run.style_skills,
                    ]
                }
            run.version_pins = pins
            session.commit()
        self.emit(
            run_id,
            "analyze.done",
            "analyze",
            "progress",
            "文档分析完成",
            f"类型 {result['document_type']} / 领域 {result['domain']}",
        )

    async def _stage_terminology(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            pins = dict(run.version_pins)
            if "term_candidates" in pins:
                return
            source = run.source_text
            confidentiality = run.confidentiality
            manual_terms = list(run.manual_terms or [])
            source_language = run.source_language
            target_language = run.target_language
        result = await llm.call_role(
            tofu=self.tofu,
            settings=self.settings,
            role="term_extractor",
            prompt_name="term_extract",
            variables={
                "source_text": source,
                **prompt_language_variables(source_language, target_language),
            },
            schema_name="term_extract",
            model=self.settings.fast_model,
            run_id=run_id,
        )
        # Analysis and term extraction run in parallel. Re-read the domain
        # after the model call so terminology lookup benefits from analysis
        # whenever that concurrent task has already completed.
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            analysis_domain = ((run.version_pins or {}).get("analysis") or {}).get("domain")
        db_hits = term_service.terms_in_text(
            source,
            domain=analysis_domain,
            source_language=source_language,
            target_language=target_language,
        )
        manual_hits = {
            item["source"].strip(): {
                "target": item["target"].strip(),
                "origin": "manual_run",
                "proper_name": bool(item.get("proper_name")),
            }
            for item in manual_terms
            if item.get("source", "").strip()
            and item.get("target", "").strip()
            and item["source"].strip() in source
        }
        # Mandatory lexical choices come only from the editable terminology
        # store or this run's manual overrides. Style Skills never inject terms.
        binding_hits = {**db_hits, **manual_hits}
        raw_terms: list[dict[str, Any]] = [
            {
                "source": source_term,
                "proposed_target": binding_hits[source_term]["target"],
                "proper_name": bool(binding_hits[source_term].get("proper_name")),
                "needs_official_check": False,
            }
            for source_term in sorted(binding_hits, key=len, reverse=True)
        ]
        binding_count = len(raw_terms)
        seen_terms = {term["source"] for term in raw_terms}
        for term in result["terms"]:
            source_term = term.get("source", "").strip()
            if (
                not source_term
                or source_term not in source
                or source_term in seen_terms
                or re.fullmatch(r"[\d\s年月日%.,，.]+", source_term)
            ):
                continue
            raw_terms.append(term)
            seen_terms.add(source_term)
        # The cap limits model suggestions only. Human/database constraints
        # are binding and must never be dropped from a terminology-dense file.
        raw_terms = (
            raw_terms[:binding_count]
            + raw_terms[binding_count : binding_count + self.settings.max_term_candidates]
        )
        verified_hits = binding_hits
        guard = QueryLeakGuard(confidentiality)

        official_candidates = sorted(
            (
                term
                for term in raw_terms
                if term.get("needs_official_check") and term["source"] not in verified_hits
            ),
            key=lambda term: (not bool(term.get("proper_name")), -len(term["source"])),
        )
        official_search_sources = {
            term["source"]
            for term in official_candidates[: self.settings.max_official_search_terms]
        }

        semaphore = asyncio.Semaphore(self.settings.research_concurrency)

        async def research_term(term: dict[str, Any]) -> dict[str, Any]:
            source_term = term["source"]
            if source_term in verified_hits:
                return {
                    "source": source_term,
                    **verified_hits[source_term],
                    "evidence": [],
                    "mandatory": True,
                }
            entry: dict[str, Any] = {
                "source": source_term,
                "target": (
                    term["proposed_target"]
                    if term.get("proper_name") or target_language != "en"
                    else _normalize_common_term_case(term["proposed_target"])
                ),
                "origin": "llm_proposed",
                "evidence": [],
                "mandatory": False,
                "proper_name": bool(term.get("proper_name")),
            }
            if (
                term.get("needs_official_check")
                and source_term in official_search_sources
                and source_language == "zh"
                and target_language == "en"
            ):
                try:
                    async with semaphore:
                        hits = await official_search(source_term, guard=guard, max_results=4)
                    entry["evidence"] = hits[:2]
                    if _evidence_supports_target(hits, entry["target"]):
                        entry["origin"] = "official_search"
                        entry["mandatory"] = True
                except LeakGuardError as exc:
                    entry["evidence"] = [{"note": str(exc)}]
            return entry

        candidates = await asyncio.gather(*(research_term(term) for term in raw_terms))
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            pins = dict(run.version_pins)
            pins["term_candidates"] = candidates
            run.version_pins = pins
            session.commit()
        verified = sum(1 for c in candidates if c.get("mandatory"))
        self.emit(
            run_id,
            "terminology.done",
            "terminology",
            "progress",
            "术语研究完成",
            f"{len(candidates)} 个候选术语，{verified} 个有官方/库内依据",
            evidence=[e for c in candidates for e in c.get("evidence", [])][:5],
            metrics={
                "candidate_count": len(candidates),
                "official_search_count": len(official_search_sources),
                "official_search_cap": self.settings.max_official_search_terms,
            },
        )

    async def _stage_retrieve(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            pins = dict(run.version_pins)
            if "references" in pins:
                return
            segments = [(s.id, s.source) for s in run.segments]
            document_type = run.document_type
            analysis_domain = (pins.get("analysis") or {}).get("domain")
            source_language = run.source_language
            target_language = run.target_language
        references: dict[str, list[dict]] = {}
        for seg_id, text in segments:
            references[seg_id] = await asyncio.to_thread(
                term_service.tm_search,
                text,
                source_language=source_language,
                target_language=target_language,
                document_type=document_type,
                domain=analysis_domain,
                top_k=3,
            )
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            pins = dict(run.version_pins)
            pins["references"] = references
            run.version_pins = pins
            session.commit()
        hits = sum(len(v) for v in references.values())
        flattened = [hit for rows in references.values() for hit in rows]
        verified = sum(hit.get("kind") == "verified_memory" for hit in flattened)
        automatic = sum(hit.get("kind") == "official_corpus" for hit in flattened)
        self.emit(
            run_id,
            "retrieve.done",
            "retrieve",
            "progress",
            "官方参考已准备",
            f"命中 {hits} 条软参考（自动语料 {automatic}，人工核验 {verified}）",
            evidence=flattened[:12],
            metrics={
                "reference_hits": hits,
                "automatic_corpus_hits": automatic,
                "human_verified_hits": verified,
            },
        )

    async def _stage_plan(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            candidates = run.version_pins.get("term_candidates", [])
            has_glossary = (
                session.query(DocumentGlossary).filter_by(run_id=run_id).first() is not None
            )
            if has_glossary:
                return
            entries = [
                {
                    "source": c["source"],
                    "target": c["target"],
                    "origin": c.get("origin", "llm_proposed"),
                    "evidence": c.get("evidence", []),
                    "mandatory": bool(c.get("mandatory")),
                    "proper_name": c.get("proper_name"),
                    "exception": None,
                }
                for c in candidates
                if c.get("source") and c.get("target")
            ]
            session.add(DocumentGlossary(run_id=run_id, version=1, entries=entries))
            session.commit()
            count = len(entries)
        self.emit(
            run_id,
            "plan.glossary",
            "plan",
            "progress",
            "文档术语表已生成",
            f"{count} 条术语约束",
            metrics={"glossary_size": count},
        )

    async def _stage_translate(self, run_id: str) -> None:
        early_task = self._early_translation_tasks.pop(run_id, None)
        if early_task:
            try:
                await early_task
            except asyncio.CancelledError:
                if self._is_cancelled(run_id):
                    return
                raise
            except Exception as exc:
                # Early execution is an optimization, never a new failure
                # mode. The ordinary stage resumes from any persisted partial
                # batches using the same idempotent implementation.
                logger.warning("run=%s early translation fallback: %s", run_id, exc)
        await self._stage_translate_impl(run_id, early=False)

    async def _stage_translate_impl(self, run_id: str, *, early: bool) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            segments = [
                {
                    "id": s.id,
                    "idx": s.idx,
                    "source": s.source,
                    "translation": s.translation,
                    "status": s.status,
                }
                for s in run.segments
            ]
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id)
                .order_by(DocumentGlossary.version.desc())
                .first()
            )
            glossary = glossary_row.entries if glossary_row else []
            references = run.version_pins.get("references", {})
            analysis = run.version_pins.get("analysis", {})
            style_skills = list(run.style_skills or [])
            translation_mode = run.translation_mode or "coherent"
            source_language = run.source_language
            target_language = run.target_language
        style_rules = render_style_contract(
            style_skills,
            source_language=source_language,
            target_language=target_language,
        )
        language_variables = prompt_language_variables(source_language, target_language)
        total = len(segments)
        if not any(segment["status"] == "pending" for segment in segments):
            return
        batches = _build_contiguous_batches(segments, self.settings.translation_batch_max_chars)
        # Coherent mode is intentionally sequential: each next section sees
        # the accepted translation of the previous section. Balanced mode is
        # an explicit latency trade-off and still shares global analysis.
        concurrency = (
            1 if translation_mode == "coherent" else self.settings.translation_batch_concurrency
        )
        semaphore = asyncio.Semaphore(concurrency)
        progress_lock = asyncio.Lock()
        translated_count = sum(segment["status"] != "pending" for segment in segments)

        async def do_batch(batch_number: int, batch: list[dict[str, Any]]) -> None:
            nonlocal translated_count
            async with semaphore:
                if self._is_cancelled(run_id):
                    return
                pending_ids = {item["id"] for item in batch if item["status"] == "pending"}
                if not pending_ids:
                    return
                context = _context_window(segments, batch, self.settings.document_context_max_chars)
                evidence = [hit for item in batch for hit in references.get(item["id"], [])][:12]
                result = await llm.call_role(
                    tofu=self.tofu,
                    settings=self.settings,
                    role="translator",
                    prompt_name="translate_batch",
                    variables={
                        **language_variables,
                        "document_analysis": analysis,
                        "document_context": [
                            {
                                "id": _model_segment_id(item),
                                "index": item["idx"] + 1,
                                "source": item["source"],
                                "translation": item["translation"],
                            }
                            for item in context
                        ],
                        "glossary": glossary,
                        "references": evidence,
                        "style_rules": style_rules,
                        "segments": [
                            {
                                "id": _model_segment_id(item),
                                "index": item["idx"] + 1,
                                "source": item["source"],
                                "needs_translation": item["id"] in pending_ids,
                                "current_translation": item["translation"],
                            }
                            for item in batch
                        ],
                    },
                    schema_name="translation_batch",
                    model=self.settings.translator_model,
                    run_id=run_id,
                )
                translated = result.get("segments", [])
                resolved_ids = [
                    _resolve_model_segment_id(item.get("id"), batch)[0] for item in translated
                ]
                if (
                    None in resolved_ids
                    or len(resolved_ids) != len(set(resolved_ids))
                    or set(resolved_ids) != pending_ids
                ):
                    raise ValueError(
                        "联合翻译返回的段落范围与请求不一致；"
                        f" expected={len(pending_ids)}, got={len(resolved_ids)}"
                    )
                by_id = {
                    segment_id: item["translation"].strip()
                    for segment_id, item in zip(resolved_ids, translated)
                    if segment_id is not None
                }
                with SessionLocal() as session:
                    for item in batch:
                        if item["id"] not in pending_ids:
                            continue
                        translation = by_id[item["id"]]
                        seg = session.get(Segment, item["id"])
                        seg.translation = translation
                        seg.versions = {**seg.versions, "ai_draft": translation}
                        seg.status = "translated"
                        # Make the accepted lead-in visible to the next batch.
                        item["translation"] = translation
                        item["status"] = "translated"
                    session.commit()
                async with progress_lock:
                    translated_count += len(pending_ids)
                    progress = (STAGE_INDEX["translate"] + translated_count / max(total, 1)) / len(
                        STAGES
                    )
                    with SessionLocal() as session:
                        run = session.get(TranslationRun, run_id)
                        if run and run.status not in RunStatus.TERMINAL:
                            run.progress = progress
                            session.commit()
                batch_ids = [item["id"] for item in batch if item["id"] in pending_ids]
                label = "全文" if len(batches) == 1 else f"连续章节 {batch_number}/{len(batches)}"
                self.emit(
                    run_id,
                    "translate.early_batch" if early else "translate.batch",
                    "translate",
                    "progress",
                    (
                        f"{label} 初译已在预处理期间生成"
                        if early
                        else f"{label} 已联合翻译"
                    ),
                    (result.get("uncertainties") or [None])[0],
                    progress=progress,
                    segment_ids=batch_ids,
                    metrics={
                        "batch": batch_number,
                        "batch_count": len(batches),
                        "paragraphs": len(batch_ids),
                        "mode": translation_mode,
                        "early": early,
                    },
                )

        if concurrency == 1:
            for batch_number, batch in enumerate(batches, 1):
                await do_batch(batch_number, batch)
        else:
            tasks = [
                asyncio.create_task(do_batch(batch_number, batch))
                for batch_number, batch in enumerate(batches, 1)
            ]
            try:
                await asyncio.gather(*tasks)
            except BaseException:
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                raise
        if early and not self._is_cancelled(run_id):
            self.emit(
                run_id,
                "translate.early_ready",
                "translate",
                "completed",
                "短文初译已生成",
                "后端继续并行完成术语核验与多维审校",
                metrics={
                    "kind": "model",
                    "roles": ["translator"],
                    "models": [self.settings.translator_model],
                    "engine": "短文并行翻译",
                },
            )

    async def _stage_deterministic_qa(self, run_id: str) -> None:
        self._replace_issues(run_id, reviewer="deterministic_qa")
        count = 0
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id)
                .order_by(DocumentGlossary.version.desc())
                .first()
            )
            glossary = glossary_row.entries if glossary_row else []
            segments = [(s.id, s.source, s.translation) for s in run.segments]
            source_language = run.source_language
            target_language = run.target_language
        for seg_id, source, translation in segments:
            if not translation:
                continue
            findings = validators.run_deterministic(
                source,
                translation,
                glossary,
                source_language=source_language,
                target_language=target_language,
            )
            for finding in findings:
                self._add_issue(run_id, seg_id, "deterministic_qa", finding)
                count += 1
        self.emit(
            run_id,
            "qa.deterministic",
            "deterministic_qa",
            "progress",
            "确定性 QA 完成",
            f"发现 {count} 个问题",
        )

    async def _stage_term_review(self, run_id: str) -> None:
        """Deterministic glossary conformance — independent of the translator."""
        self._replace_issues(run_id, reviewer="term_reviewer")
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id)
                .order_by(DocumentGlossary.version.desc())
                .first()
            )
            glossary = glossary_row.entries if glossary_row else []
            segments = [(s.id, s.source, s.translation) for s in run.segments]
        count = 0
        for seg_id, source, translation in segments:
            if not translation:
                continue
            for finding in validators.validate_terminology(source, translation, glossary):
                self._add_issue(run_id, seg_id, "term_reviewer", finding)
                count += 1
        self.emit(
            run_id,
            "review.term",
            "term_review",
            "progress",
            "术语审校完成",
            f"发现 {count} 处术语违规",
        )

    async def _document_review_findings(
        self,
        run_id: str,
        *,
        reviewer: str,
        prompt_name: str,
        dimension: str,
        instructions: str,
    ) -> list[tuple[str, dict[str, Any]]]:
        """Review contiguous bilingual batches with document-wide context."""
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            segments = [
                {
                    "id": s.id,
                    "idx": s.idx,
                    "source": s.source,
                    "translation": s.translation or "",
                    "status": s.status,
                }
                for s in run.segments
                if s.translation
            ]
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id)
                .order_by(DocumentGlossary.version.desc())
                .first()
            )
            glossary = _binding_glossary(glossary_row.entries if glossary_row else [])
            analysis = run.version_pins.get("analysis", {})
            references = run.version_pins.get("references", {})
            style_skills = list(run.style_skills or [])
            source_language = run.source_language
            target_language = run.target_language
        if not segments:
            return []
        style_rules = render_style_contract(
            style_skills,
            source_language=source_language,
            target_language=target_language,
        )
        language_variables = prompt_language_variables(source_language, target_language)
        # Reviews carry source + target, so use half the translation input
        # budget. A typical document still stays in one joint review call.
        review_budget = max(2_000, self.settings.translation_batch_max_chars // 2)
        batches = _build_contiguous_batches(segments, review_budget)
        semaphore = asyncio.Semaphore(min(self.settings.review_concurrency, 3))

        async def review_batch(batch: list[dict[str, Any]]) -> list[tuple[str, dict[str, Any]]]:
            if self._is_cancelled(run_id):
                return []
            evidence = [hit for item in batch for hit in references.get(item["id"], [])][:12]
            async with semaphore:
                result = await llm.call_role(
                    tofu=self.tofu,
                    settings=self.settings,
                    role=reviewer,
                    prompt_name=prompt_name,
                    variables={
                        **language_variables,
                        "review_dimension": dimension,
                        "review_instructions": instructions,
                        "document_analysis": analysis,
                        "glossary": glossary,
                        "style_rules": style_rules,
                        "document_context": [
                            {
                                "id": _model_segment_id(item),
                                "index": item["idx"] + 1,
                                "source": item["source"],
                                "translation": item["translation"],
                            }
                            for item in _context_window(
                                segments, batch, self.settings.document_context_max_chars
                            )
                        ],
                        "evidence": evidence,
                        "allowed_segment_ids": [_model_segment_id(item) for item in batch],
                        "segments": [
                            {
                                "id": _model_segment_id(item),
                                "index": item["idx"] + 1,
                                "source": item["source"],
                                "translation": item["translation"],
                            }
                            for item in batch
                        ],
                    },
                    schema_name="document_review",
                    model=self.settings.review_model,
                    run_id=run_id,
                )
            findings: list[tuple[str, dict[str, Any]]] = []
            for raw in result.get("issues", []):
                supplied_id = raw.get("segment_id")
                segment_id, resolution = _resolve_model_segment_id(supplied_id, batch, raw)
                if segment_id is None:
                    logger.warning(
                        "run=%s reviewer=%s ignored issue with unresolved segment id=%r",
                        run_id,
                        reviewer,
                        supplied_id,
                    )
                    self.emit(
                        run_id,
                        "review.output_ignored",
                        dimension,
                        "progress",
                        "忽略无法定位的审校意见",
                        "该条模型意见未能唯一对应到原文段落，已安全忽略，不影响其余译文",
                        metrics={"reviewer": reviewer, "reason": "unresolved_segment_id"},
                    )
                    continue
                matched_segment = next(item for item in batch if item["id"] == segment_id)
                if validators.finding_conflicts_with_currency_anchor(
                    matched_segment["source"], matched_segment["translation"], raw
                ):
                    logger.warning(
                        "run=%s reviewer=%s ignored finding that contradicts source currency",
                        run_id,
                        reviewer,
                    )
                    self.emit(
                        run_id,
                        "review.output_ignored",
                        dimension,
                        "progress",
                        "审校意见与源文硬事实冲突",
                        "系统保留了源文明示的币种，未执行该条模型建议",
                        segment_ids=[segment_id],
                        metrics={"reviewer": reviewer, "reason": "currency_anchor_conflict"},
                    )
                    continue
                if resolution not in {"alias", "actual"}:
                    logger.info(
                        "run=%s reviewer=%s repaired segment id=%r via %s",
                        run_id,
                        reviewer,
                        supplied_id,
                        resolution,
                    )
                    self.emit(
                        run_id,
                        "review.output_repaired",
                        dimension,
                        "progress",
                        "审校定位已自动校正",
                        "模型意见已重新匹配到唯一段落",
                        segment_ids=[segment_id],
                        metrics={"reviewer": reviewer, "method": resolution},
                    )
                findings.append((segment_id, {**raw, "segment_id": segment_id}))
            return findings

        tasks = [asyncio.create_task(review_batch(batch)) for batch in batches]
        try:
            return [item for group in await asyncio.gather(*tasks) for item in group]
        except BaseException:
            for task in tasks:
                task.cancel()
            raise

    def _make_llm_reviewer(self, stage_id: str):
        dimension, instructions = REVIEW_DIMENSIONS[stage_id]

        async def _review(run_id: str) -> None:
            reviewer = f"{dimension}_reviewer"
            self._replace_issues(run_id, reviewer=reviewer)
            findings = await self._document_review_findings(
                run_id,
                reviewer=reviewer,
                prompt_name="document_review",
                dimension=dimension,
                instructions=instructions,
            )
            if self._is_cancelled(run_id):
                return
            for segment_id, raw in findings:
                self._add_issue(run_id, segment_id, reviewer, raw)
            self.emit(
                run_id,
                f"review.{dimension}",
                stage_id,
                "progress",
                f"{dimension} 联合审校完成",
                f"发现 {len(findings)} 个问题",
            )

        return _review

    async def _stage_consistency_review(self, run_id: str) -> None:
        """Deterministic terminology plus model-based document coherence."""
        self._replace_issues(run_id, reviewer="consistency_reviewer")
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id)
                .order_by(DocumentGlossary.version.desc())
                .first()
            )
            glossary = glossary_row.entries if glossary_row else []
            segments = [(s.id, s.source, s.translation or "") for s in run.segments]
        count = 0
        for entry in glossary:
            mandatory = entry.get("mandatory")
            if mandatory is None:
                mandatory = "origin" not in entry or entry.get("origin") in {
                    "term_db",
                    "official_verified",
                }
            if not mandatory or entry.get("exception"):
                continue
            src, expected = entry.get("source", ""), entry.get("target", "")
            if not src or not expected:
                continue
            variants: dict[str, list[str]] = {}
            for seg_id, source, translation in segments:
                if src in source and expected.casefold() not in translation.casefold():
                    variants.setdefault(seg_id, [])
            for seg_id in variants:
                self._add_issue(
                    run_id,
                    seg_id,
                    "consistency_reviewer",
                    {
                        "severity": "major",
                        "category": "consistency",
                        "source_span": src,
                        "target_span": expected,
                        "message": f"术语 {src} 在该段落未使用全文统一译法 “{expected}”",
                        "suggested_fix": f"统一为 {expected}",
                    },
                )
                count += 1
        if len(segments) > 1 and not self._is_cancelled(run_id):
            findings = await self._document_review_findings(
                run_id,
                reviewer="consistency_reviewer",
                prompt_name="coherence_review",
                dimension="coherence",
                instructions=(
                    "Check entity, abbreviation, reference, pronoun, subject, transition, "
                    "modality, tense and list consistency across paragraphs."
                ),
            )
            for segment_id, raw in findings:
                self._add_issue(run_id, segment_id, "consistency_reviewer", raw)
                count += 1
        self.emit(
            run_id,
            "review.consistency",
            "consistency_review",
            "progress",
            "全文一致性审校完成",
            f"发现 {count} 处不一致",
        )

    async def _stage_finalize(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            open_issues = (
                session.query(Issue)
                .filter(
                    Issue.run_id == run_id,
                    Issue.status == "open",
                    Issue.severity.in_(["critical", "major"]),
                )
                .all()
            )
            by_segment: dict[str, list[dict[str, Any]]] = {}
            for issue in open_issues:
                if issue.segment_id:
                    by_segment.setdefault(issue.segment_id, []).append(
                        {
                            "id": issue.id,
                            "reviewer": issue.reviewer,
                            "severity": issue.severity,
                            "category": issue.category,
                            "message": issue.message,
                            "suggested_fix": issue.suggested_fix,
                        }
                    )
            segments = {
                s.id: {"id": s.id, "idx": s.idx, "source": s.source, "translation": s.translation}
                for s in run.segments
            }
            loop_count = run.loop_count
            previous_revision_rounds = int(
                (run.version_pins or {}).get("previous_revision_rounds") or 0
            )
            analysis = run.version_pins.get("analysis", {})
            source_language = run.source_language
            target_language = run.target_language
            style_rules = render_style_contract(
                run.style_skills or [],
                source_language=source_language,
                target_language=target_language,
            )
            language_variables = prompt_language_variables(source_language, target_language)
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id)
                .order_by(DocumentGlossary.version.desc())
                .first()
            )
            glossary = _binding_glossary(glossary_row.entries if glossary_row else [])
        if not by_segment:
            with SessionLocal() as session:
                for seg in session.query(Segment).filter_by(run_id=run_id).all():
                    if seg.status != "final":
                        seg.versions = {
                            **seg.versions,
                            "reviewed": seg.translation,
                            "final": seg.translation,
                        }
                        seg.status = "final"
                session.commit()
            self.emit(
                run_id, "finalize.noop", "finalize", "progress", "定稿完成", "无需修改，直接定稿"
            )
            return
        escalated = loop_count > 0 or previous_revision_rounds > 0
        finalizer_model = (
            self.settings.review_model if escalated else self.settings.translator_model
        )
        finalizer_role = "finalizer_escalated" if escalated else "finalizer"
        ordered_segments = sorted(segments.values(), key=lambda item: item["idx"])
        revision_items = [
            {**segments[seg_id], "issues": issues}
            for seg_id, issues in sorted(
                by_segment.items(), key=lambda item: segments[item[0]]["idx"]
            )
        ]
        revision_budget = max(2_000, self.settings.translation_batch_max_chars // 2)
        batches = _build_contiguous_batches(revision_items, revision_budget)
        semaphore = asyncio.Semaphore(min(self.settings.review_concurrency, 3))

        async def finalize_batch(batch: list[dict[str, Any]]) -> list[dict[str, Any]]:
            expected_ids = {item["id"] for item in batch}
            context = _context_window(
                ordered_segments, batch, self.settings.document_context_max_chars
            )
            async with semaphore:
                result = await llm.call_role(
                    tofu=self.tofu,
                    settings=self.settings,
                    role=finalizer_role,
                    prompt_name="finalize_batch",
                    variables={
                        **language_variables,
                        "glossary": glossary,
                        "document_analysis": analysis,
                        "style_rules": style_rules,
                        "document_context": [
                            {
                                "id": _model_segment_id(item),
                                "index": item["idx"] + 1,
                                "source": item["source"],
                                "translation": item["translation"],
                            }
                            for item in context
                        ],
                        "allowed_segment_ids": [_model_segment_id(item) for item in batch],
                        "segments": [
                            {
                                "id": _model_segment_id(item),
                                "index": item["idx"] + 1,
                                "source": item["source"],
                                "current_translation": item["translation"],
                                "issues": [
                                    {
                                        key: issue[key]
                                        for key in (
                                            "severity",
                                            "category",
                                            "message",
                                            "suggested_fix",
                                        )
                                    }
                                    for issue in item["issues"]
                                ],
                            }
                            for item in batch
                        ],
                    },
                    schema_name="finalize_batch",
                    model=finalizer_model,
                    run_id=run_id,
                )
            returned = result.get("segments", [])
            resolved_ids = [
                _resolve_model_segment_id(item.get("id"), batch)[0] for item in returned
            ]
            if (
                None in resolved_ids
                or len(resolved_ids) != len(set(resolved_ids))
                or set(resolved_ids) != expected_ids
            ):
                raise ValueError(
                    "联合定稿返回的段落范围与请求不一致；"
                    f" expected={len(expected_ids)}, got={len(resolved_ids)}"
                )
            returned_by_id = {
                segment_id: item
                for segment_id, item in zip(resolved_ids, returned)
                if segment_id is not None
            }
            return [
                {
                    "segment": segments[item["id"]],
                    "issues": item["issues"],
                    "result": returned_by_id[item["id"]],
                }
                for item in batch
            ]

        tasks = [asyncio.create_task(finalize_batch(batch)) for batch in batches]
        try:
            finalized = [item for group in await asyncio.gather(*tasks) for item in group]
        except BaseException:
            for task in tasks:
                task.cancel()
            raise
        if self._is_cancelled(run_id):
            return

        # A generative repair may not introduce a new deterministic blocker.
        # If it does, preserve the known-safe text and dismiss only the model
        # opinions that led to the unsafe rewrite. Objective QA findings remain
        # open so the bounded repair loop can try again.
        for item in finalized:
            segment = item["segment"]
            result = item["result"]
            current_text = segment["translation"] or ""
            proposed_text = result["final_translation"]
            current_blockers = {
                (finding.get("category"), finding.get("source_span"))
                for finding in validators.run_deterministic(
                    segment["source"],
                    current_text,
                    glossary,
                    source_language=source_language,
                    target_language=target_language,
                )
                if finding.get("severity") in {"critical", "major"}
            }
            proposed_findings = validators.run_deterministic(
                segment["source"],
                proposed_text,
                glossary,
                source_language=source_language,
                target_language=target_language,
            )
            introduced = [
                finding
                for finding in proposed_findings
                if finding.get("severity") in {"critical", "major"}
                and (finding.get("category"), finding.get("source_span"))
                not in current_blockers
            ]
            if introduced:
                item["result"] = {
                    **result,
                    "final_translation": current_text,
                    "changes": [],
                    "_rejected": True,
                    "_rejected_categories": sorted(
                        {str(finding.get("category") or "other") for finding in introduced}
                    ),
                }

        with SessionLocal() as session:
            finalized_ids = {item["segment"]["id"] for item in finalized}
            for item in finalized:
                segment = item["segment"]
                issues = item["issues"]
                result = item["result"]
                final_text = result["final_translation"]
                seg_id = segment["id"]
                seg_row = session.get(Segment, seg_id)
                seg_row.versions = {
                    **seg_row.versions,
                    "reviewed": seg_row.versions.get("reviewed") or seg_row.translation,
                    "final": final_text,
                }
                seg_row.translation = final_text
                seg_row.status = "final"
                for issue in session.query(Issue).filter(
                    Issue.id.in_([i["id"] for i in issues])
                ).all():
                    if result.get("_rejected") and issue.reviewer in {
                        "semantic_reviewer",
                        "style_reviewer",
                        "consistency_reviewer",
                    }:
                        issue.status = "dismissed"
                    elif not result.get("_rejected"):
                        issue.status = "resolved"
            # A document can have one repaired paragraph and many untouched
            # paragraphs. Successful finalization publishes the whole text,
            # not only the rows that happened to carry blocking issues.
            for seg_row in session.query(Segment).filter_by(run_id=run_id).all():
                if seg_row.id in finalized_ids or seg_row.status == "final":
                    continue
                seg_row.versions = {
                    **seg_row.versions,
                    "reviewed": seg_row.versions.get("reviewed") or seg_row.translation,
                    "final": seg_row.translation,
                }
                seg_row.status = "final"
            session.commit()

        for item in finalized:
            segment = item["segment"]
            issues = item["issues"]
            result = item["result"]
            rejected = bool(result.get("_rejected"))
            self.emit(
                run_id,
                "finalize.segment",
                "finalize",
                "progress",
                f"段落 {segment['idx'] + 1} 已定稿",
                (
                    "候选修订会引入新的硬性错误，已保留原译文并驳回冲突意见"
                    if rejected
                    else f"解决 {len(issues)} 个问题，{len(result.get('changes', []))} 处修改"
                ),
                segment_ids=[segment["id"]],
                metrics={
                    "changes": result.get("changes", []),
                    "rejected": rejected,
                    "rejected_categories": result.get("_rejected_categories", []),
                },
            )

    async def _stage_final_qa(self, run_id: str) -> None:
        """Deterministic release gate after independent model review/finalize.

        Semantic, style and coherence have already been reviewed immediately
        before finalization. Re-running another unconstrained model reviewer
        here made the gate stochastic: each pass could invent a new preference
        and trigger another rewrite. The release gate therefore checks only
        objective, reproducible invariants and loops back solely for concrete
        repairable failures.
        """
        self._replace_issues(run_id, reviewer="final_qa")
        # Clear any open legacy release-review rows when resuming a run created
        # by an earlier pipeline version.
        self._replace_issues(run_id, reviewer="final_release_reviewer")
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id)
                .order_by(DocumentGlossary.version.desc())
                .first()
            )
            glossary = glossary_row.entries if glossary_row else []
            segments = [(s.id, s.idx, s.source, s.translation) for s in run.segments]
            source_language = run.source_language
            target_language = run.target_language
        severity_counts = {"critical": 0, "major": 0, "minor": 0}
        for seg_id, _idx, source, translation in segments:
            if not translation:
                finding = {
                    "severity": "critical",
                    "category": "completeness",
                    "source_span": source[:50],
                    "target_span": "",
                    "message": "段落缺少译文",
                    "suggested_fix": "补译",
                }
                self._add_issue(run_id, seg_id, "final_qa", finding)
                severity_counts["critical"] += 1
                continue
            for finding in validators.run_deterministic(
                source,
                translation,
                glossary,
                source_language=source_language,
                target_language=target_language,
            ):
                self._add_issue(run_id, seg_id, "final_qa", finding)
                severity = finding.get("severity", "minor")
                severity_counts[severity] = severity_counts.get(severity, 0) + 1

        blocking = severity_counts["critical"] + severity_counts["major"]
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if blocking > 0:
                if run.loop_count < self.settings.max_finalize_loops:
                    run.loop_count += 1
                    run.current_stage = "finalize"  # loop back
                    run.status = RunStatus.FINALIZING
                    decision = (
                        f"发现 {blocking} 个发布阻断问题，自动进入第 {run.loop_count} 轮增强修订"
                    )
                else:
                    run.status = RunStatus.QUALITY_GATE_FAILED
                    run.current_stage = "final_qa"
                    run.progress = 1.0
                    decision = (
                        f"自动增强修订已达上限，仍有 {blocking} 个发布阻断问题；"
                        "译文与审校依据已保存，本轮暂停，可继续自动优化。"
                    )
                    run.error = decision
            else:
                run.current_stage = "complete"
                run.error = None
                decision = "确定性发布检查通过"
            quality_failed = run.status == RunStatus.QUALITY_GATE_FAILED
            final_progress = run.progress
            session.commit()
        self.emit(
            run_id,
            "qa.final",
            "final_qa",
            "progress",
            "终审 QA",
            decision,
            progress=final_progress,
            metrics={**severity_counts, "blocking_open": blocking},
        )
        if quality_failed:
            self.emit(
                run_id,
                "run.quality_gate_failed",
                "final_qa",
                "failed",
                "自动质量闸门未通过",
                decision,
                metrics={**severity_counts, "blocking_open": blocking},
            )

    async def _stage_complete(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            missing = (
                session.query(Segment)
                .filter(Segment.run_id == run_id, Segment.translation.is_(None))
                .count()
            )
            blocking = (
                session.query(Issue)
                .filter(
                    Issue.run_id == run_id,
                    Issue.status == "open",
                    Issue.severity.in_(["critical", "major"]),
                )
                .count()
            )
            if blocking or missing:
                run.status = RunStatus.QUALITY_GATE_FAILED
                run.progress = 1.0
                run.error = (
                    f"发布前检查发现 {blocking} 个未解决的阻断问题、{missing} 个缺失译文的段落"
                )
                session.commit()
                self.emit(
                    run_id,
                    "run.quality_gate_failed",
                    "complete",
                    "failed",
                    "自动质量闸门未通过",
                    run.error,
                )
                return
            for segment in run.segments:
                if segment.status != "final":
                    segment.versions = {
                        **segment.versions,
                        "reviewed": segment.versions.get("reviewed") or segment.translation,
                        "final": segment.translation,
                    }
                    segment.status = "final"
            run.status = RunStatus.COMPLETED
            run.progress = 1.0
            session.commit()
        self.emit(
            run_id,
            "run.completed",
            "complete",
            "completed",
            "翻译完成",
            "全部阶段执行完毕",
            progress=1.0,
        )

    # ----------------------------------------------------------------- helpers
    def _replace_issues(self, run_id: str, *, reviewer: str) -> None:
        """Idempotent review: a reviewer re-run replaces its own open issues."""
        with SessionLocal() as session:
            session.query(Issue).filter(
                Issue.run_id == run_id, Issue.reviewer == reviewer, Issue.status == "open"
            ).delete(synchronize_session=False)
            session.commit()

    def _add_issue(self, run_id: str, segment_id: str | None, reviewer: str, finding: dict) -> None:
        finding = _normalize_finding(finding)
        with SessionLocal() as session:
            session.add(
                Issue(
                    id=uuid.uuid4().hex,
                    run_id=run_id,
                    segment_id=segment_id,
                    reviewer=reviewer,
                    severity=finding.get("severity", "minor"),
                    category=finding.get("category", "other"),
                    source_span=finding.get("source_span", ""),
                    target_span=finding.get("target_span", ""),
                    message=finding.get("message", ""),
                    suggested_fix=finding.get("suggested_fix"),
                    evidence_refs=finding.get("evidence_refs", []),
                )
            )
            session.commit()
