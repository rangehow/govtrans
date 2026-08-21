"""Translation Orchestrator — executes the deterministic stage graph.

Guarantees:
- idempotent stages: re-running a completed stage is a no-op, so a run
  resumes after server restart from run.current_stage (§8);
- every stage emits persisted SSE events (started/progress/completed/failed);
- cancellation is honored between stages and between segments;
- release gate: critical issues after final_qa loop back to finalize, up to
  settings.max_finalize_loops, then WAITING_HUMAN_REVIEW (§26).
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Callable

from apps.api.config import Settings
from apps.api.db import SessionLocal
from agents.roles import llm
from services.orchestrator.events import RunEventOut
from services.orchestrator.models import (
    DocumentGlossary,
    Issue,
    RunEvent,
    RunStatus,
    Segment,
    TranslationRun,
)
from services.orchestrator.skills import DEFAULT_SKILLS, load_skill_rules, skill_version
from services.orchestrator.stage_graph import STAGES, STAGE_INDEX, Stage
from services.orchestrator.tofu_client import TofuClient
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
        "Official government register in English: formal, declarative, concise; "
        "consistent with White Paper / policy document style; no colloquialisms.",
    ),
}


def _utcnow_iso() -> datetime:
    return datetime.now(timezone.utc)


class Orchestrator:
    def __init__(self, settings: Settings, tofu: TofuClient) -> None:
        self.settings = settings
        self.tofu = tofu
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        self._tasks: dict[str, asyncio.Task] = {}

    # ------------------------------------------------------------- lifecycle
    def create_run(
        self,
        *,
        source_text: str,
        confidentiality: str,
        document_type: str | None = None,
        direction: str = "zh-en",
    ) -> str:
        with SessionLocal() as session:
            run = TranslationRun(
                source_text=source_text,
                confidentiality=confidentiality,
                document_type=document_type,
                direction=direction,
                pipeline_version=self.settings.pipeline_version,
                version_pins=self._version_pins(),
            )
            session.add(run)
            session.commit()
            run_id = run.id
        self.emit(run_id, "run.created", "run", "started", "运行已创建",
                  f"机密分级 {confidentiality}")
        return run_id

    def _version_pins(self) -> dict[str, Any]:
        return {
            "pipeline_version": self.settings.pipeline_version,
            "translator_model": self.settings.translator_model,
            "review_model": self.settings.review_model,
            "fast_model": self.settings.fast_model,
            "prompt_versions": {
                name: llm.prompt_version(name)
                for name in ("analyze", "term_extract", "translate_segment", "review", "finalize")
            },
            "skill_versions": {name: skill_version(name) for name in DEFAULT_SKILLS},
        }

    def start(self, run_id: str) -> None:
        if run_id in self._tasks and not self._tasks[run_id].done():
            return
        self._tasks[run_id] = asyncio.create_task(self._guarded_execute(run_id))

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
        with SessionLocal() as session:
            last_seq = (
                session.query(RunEvent.seq)
                .filter(RunEvent.run_id == run_id)
                .order_by(RunEvent.seq.desc())
                .first()
            )
            seq = (last_seq[0] if last_seq else 0) + 1
            row = RunEvent(
                run_id=run_id, seq=seq, type=type_, phase=phase, status=status,
                title=title, summary=summary, progress=progress,
                segment_ids=segment_ids or [], evidence=evidence or [], metrics=metrics or {},
            )
            session.add(row)
            session.commit()
            event = RunEventOut(
                id=row.id, run_id=run_id, seq=seq, type=type_, phase=phase, status=status,
                title=title, summary=summary, progress=progress,
                segment_ids=row.segment_ids, evidence=row.evidence, metrics=row.metrics,
                created_at=row.created_at or _utcnow_iso(),
            )
        for queue in list(self._subscribers.get(run_id, ())):
            queue.put_nowait(event)
        return event

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subscribers.setdefault(run_id, set()).add(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        self._subscribers.get(run_id, set()).discard(queue)

    # -------------------------------------------------------------- execution
    async def _guarded_execute(self, run_id: str) -> None:
        try:
            await self.execute(run_id)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # every failure must be observable (§51)
            logger.exception("run %s failed", run_id)
            with SessionLocal() as session:
                run = session.get(TranslationRun, run_id)
                if run and run.status not in RunStatus.TERMINAL:
                    run.status = RunStatus.FAILED
                    run.error = f"{type(exc).__name__}: {exc}"[:2000]
                    session.commit()
            self.emit(run_id, "run.failed", "run", "failed", "运行失败",
                      f"{type(exc).__name__}: {exc}"[:500])

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
                if run is None or run.status in RunStatus.TERMINAL:
                    return
            if self._is_cancelled(run_id):
                return
            await self._run_stage(run_id, stage, idx)
            idx += 1
            # Release-gate loop: final_qa decided to go back to finalize.
            with SessionLocal() as session:
                run = session.get(TranslationRun, run_id)
                if run is None or run.status in RunStatus.TERMINAL:
                    return
                next_stage = run.current_stage
            if next_stage and STAGE_INDEX.get(next_stage, 0) < idx:
                idx = STAGE_INDEX[next_stage]

    def _is_cancelled(self, run_id: str) -> bool:
        with SessionLocal() as session:
            status = session.get(TranslationRun, run_id).status
        return status == RunStatus.CANCELLED

    async def _run_stage(self, run_id: str, stage: Stage, idx: int) -> None:
        progress_base = idx / len(STAGES)
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            run.status = stage.run_status if stage.id != "complete" else run.status
            run.current_stage = stage.id
            run.progress = progress_base
            session.commit()
        self.emit(run_id, f"stage.{stage.id}", stage.id, "started", stage.title, None,
                  progress=progress_base)
        executor = self._executors()[stage.id]
        await executor(run_id)
        self.emit(run_id, f"stage.{stage.id}", stage.id, "completed", stage.title, None,
                  progress=(idx + 1) / len(STAGES))
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            run.progress = (idx + 1) / len(STAGES)
            # Executors may redirect the flow (release-gate loop in final_qa
            # points current_stage back at finalize) — only auto-advance when
            # the stage did not redirect.
            if run.current_stage == stage.id:
                run.current_stage = STAGES[idx + 1].id if idx + 1 < len(STAGES) else "complete"
            session.commit()

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
            chunks = [c.strip() for c in run.source_text.split("\n") if c.strip()]
            if not chunks:
                raise ValueError("源文为空，无法解析")
            for i, chunk in enumerate(chunks):
                session.add(Segment(run_id=run_id, idx=i, source=chunk))
            session.commit()
            count = len(chunks)
        self.emit(run_id, "parse.segments", "parse", "progress", "句段切分完成",
                  f"共 {count} 个句段")

    async def _stage_analyze(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if run.summary:
                return
            source = run.source_text
        result = await llm.call_role(
            tofu=self.tofu, settings=self.settings, role="analyst",
            prompt_name="analyze", variables={"source_text": source},
            schema_name="analyze", model=self.settings.fast_model, run_id=run_id,
        )
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            run.summary = result["summary"]
            run.document_type = run.document_type or result["document_type"]
            pins = dict(run.version_pins)
            pins["analysis"] = result
            run.version_pins = pins
            session.commit()
        self.emit(run_id, "analyze.done", "analyze", "progress", "文档分析完成",
                  f"类型 {result['document_type']} / 领域 {result['domain']}")

    async def _stage_terminology(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            pins = dict(run.version_pins)
            if "term_candidates" in pins:
                return
            source = run.source_text
            confidentiality = run.confidentiality
        result = await llm.call_role(
            tofu=self.tofu, settings=self.settings, role="term_extractor",
            prompt_name="term_extract", variables={"source_text": source},
            schema_name="term_extract", model=self.settings.fast_model, run_id=run_id,
        )
        candidates: list[dict[str, Any]] = []
        db_hits = term_service.term_lookup([t["source"] for t in result["terms"]])
        guard = QueryLeakGuard(confidentiality)
        for term in result["terms"]:
            source_term = term["source"]
            if source_term in db_hits:
                candidates.append({"source": source_term, **db_hits[source_term]})
                continue
            entry: dict[str, Any] = {
                "source": source_term,
                "target": term["proposed_target"],
                "origin": "llm_proposed",
                "evidence": [],
            }
            if term.get("needs_official_check"):
                try:
                    hits = await official_search(source_term, guard=guard, max_results=3)
                    entry["evidence"] = hits[:2]
                    if hits:
                        entry["origin"] = "official_search"
                except LeakGuardError as exc:
                    entry["evidence"] = [{"note": str(exc)}]
            candidates.append(entry)
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            pins = dict(run.version_pins)
            pins["term_candidates"] = candidates
            run.version_pins = pins
            session.commit()
        verified = sum(1 for c in candidates if c.get("origin") != "llm_proposed")
        self.emit(run_id, "terminology.done", "terminology", "progress", "术语研究完成",
                  f"{len(candidates)} 个候选术语，{verified} 个有官方/库内依据",
                  evidence=[e for c in candidates for e in c.get("evidence", [])][:5])

    async def _stage_retrieve(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            pins = dict(run.version_pins)
            if "references" in pins:
                return
            segments = [(s.id, s.source) for s in run.segments]
            document_type = run.document_type
        references: dict[str, list[dict]] = {}
        for seg_id, text in segments:
            references[seg_id] = term_service.tm_search(
                text, document_type=document_type, top_k=3
            )
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            pins = dict(run.version_pins)
            pins["references"] = references
            run.version_pins = pins
            session.commit()
        hits = sum(len(v) for v in references.values())
        self.emit(run_id, "retrieve.done", "retrieve", "progress", "参考准备完成",
                  f"翻译记忆命中 {hits} 条")

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
                {"source": c["source"], "target": c["target"],
                 "origin": c.get("origin", "llm_proposed"),
                 "evidence": c.get("evidence", []), "exception": None}
                for c in candidates
                if c.get("source") and c.get("target")
            ]
            session.add(DocumentGlossary(run_id=run_id, version=1, entries=entries))
            session.commit()
            count = len(entries)
        self.emit(run_id, "plan.glossary", "plan", "progress", "文档术语表已生成",
                  f"{count} 条术语约束", metrics={"glossary_size": count})

    async def _stage_translate(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            segments = [(s.id, s.idx, s.source, s.translation, s.status) for s in run.segments]
            summary = run.summary or ""
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id).order_by(DocumentGlossary.version.desc()).first()
            )
            glossary = glossary_row.entries if glossary_row else []
            references = run.version_pins.get("references", {})
        style_rules = "\n\n".join(
            f"### {name}\n{load_skill_rules(name)}" for name in DEFAULT_SKILLS
        )
        total = len(segments)
        pending = [(s, i, src) for s, i, src, _t, st in segments if st == "pending"]
        if not pending:  # idempotent resume: everything already translated
            return
        # Bounded concurrency (E10): LLM calls dominate wall time. Context for
        # cohesion is the previous segment's SOURCE (always available) — the
        # summary carries global cohesion; see docs/TRANSLATION_PIPELINE.md.
        semaphore = asyncio.Semaphore(4)

        async def do_segment(seg_id: str, idx: int, source: str) -> None:
            async with semaphore:
                if self._is_cancelled(run_id):
                    return
                previous_context = segments[idx - 1][2] if idx > 0 else "(首段)"
                result = await llm.call_role(
                    tofu=self.tofu, settings=self.settings, role="translator",
                    prompt_name="translate_segment",
                    variables={
                        "summary": summary,
                        "section_context": f"第 {idx + 1}/{total} 段",
                        "previous_context": previous_context,
                        "glossary": glossary,
                        "references": references.get(seg_id, []),
                        "style_rules": style_rules,
                        "source_segment": source,
                    },
                    schema_name="translation", model=self.settings.translator_model,
                    run_id=run_id,
                )
                with SessionLocal() as session:
                    seg = session.get(Segment, seg_id)
                    seg.translation = result["translation"]
                    seg.versions = {"ai_draft": result["translation"]}
                    seg.status = "translated"
                    session.commit()
                self.emit(run_id, "translate.segment", "translate", "progress",
                          f"句段 {idx + 1}/{total} 已翻译",
                          (result.get("uncertainties") or [None])[0],
                          segment_ids=[seg_id])

        tasks = [asyncio.create_task(do_segment(s, i, src)) for s, i, src in pending]
        try:
            await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            raise

    async def _stage_deterministic_qa(self, run_id: str) -> None:
        self._replace_issues(run_id, reviewer="deterministic_qa")
        count = 0
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id).order_by(DocumentGlossary.version.desc()).first()
            )
            glossary = glossary_row.entries if glossary_row else []
            segments = [(s.id, s.source, s.translation) for s in run.segments]
        for seg_id, source, translation in segments:
            if not translation:
                continue
            findings = validators.run_deterministic(source, translation, glossary)
            for finding in findings:
                self._add_issue(run_id, seg_id, "deterministic_qa", finding)
                count += 1
        self.emit(run_id, "qa.deterministic", "deterministic_qa", "progress",
                  "确定性 QA 完成", f"发现 {count} 个问题")

    async def _stage_term_review(self, run_id: str) -> None:
        """Deterministic glossary conformance — independent of the translator."""
        self._replace_issues(run_id, reviewer="term_reviewer")
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id).order_by(DocumentGlossary.version.desc()).first()
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
        self.emit(run_id, "review.term", "term_review", "progress",
                  "术语审校完成", f"发现 {count} 处术语违规")

    def _make_llm_reviewer(self, stage_id: str):
        dimension, instructions = REVIEW_DIMENSIONS[stage_id]

        async def _review(run_id: str) -> None:
            reviewer = f"{dimension}_reviewer"
            self._replace_issues(run_id, reviewer=reviewer)
            with SessionLocal() as session:
                run = session.get(TranslationRun, run_id)
                segments = [(s.id, s.idx, s.source, s.translation) for s in run.segments]
                glossary_row = (
                    session.query(DocumentGlossary)
                    .filter_by(run_id=run_id).order_by(DocumentGlossary.version.desc()).first()
                )
                glossary = glossary_row.entries if glossary_row else []
                references = run.version_pins.get("references", {})
            total_issues = 0
            for seg_id, idx, source, translation in segments:
                if self._is_cancelled(run_id):
                    return
                if not translation:
                    continue
                result = await llm.call_role(
                    tofu=self.tofu, settings=self.settings, role=reviewer,
                    prompt_name="review",
                    variables={
                        "review_dimension": dimension,
                        "review_instructions": instructions,
                        "glossary": glossary,
                        "evidence": references.get(seg_id, []),
                        "source": source,
                        "translation": translation,
                    },
                    schema_name="review", model=self.settings.review_model, run_id=run_id,
                )
                for raw in result.get("issues", []):
                    self._add_issue(run_id, seg_id, reviewer, raw)
                    total_issues += 1
            self.emit(run_id, f"review.{dimension}", stage_id, "progress",
                      f"{dimension} 审校完成", f"发现 {total_issues} 个问题")

        return _review

    async def _stage_consistency_review(self, run_id: str) -> None:
        """Cross-segment consistency: one source term must map to one target."""
        self._replace_issues(run_id, reviewer="consistency_reviewer")
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id).order_by(DocumentGlossary.version.desc()).first()
            )
            glossary = glossary_row.entries if glossary_row else []
            segments = [(s.id, s.source, s.translation or "") for s in run.segments]
        count = 0
        for entry in glossary:
            src, expected = entry.get("source", ""), entry.get("target", "")
            if not src or not expected:
                continue
            variants: dict[str, list[str]] = {}
            for seg_id, source, translation in segments:
                if src in source and expected.lower() not in translation.lower():
                    variants.setdefault(seg_id, [])
            for seg_id in variants:
                self._add_issue(run_id, seg_id, "consistency_reviewer", {
                    "severity": "major", "category": "consistency",
                    "source_span": src, "target_span": expected,
                    "message": f"术语 {src} 在该句段未使用全文统一译法 “{expected}”",
                    "suggested_fix": f"统一为 {expected}",
                })
                count += 1
        self.emit(run_id, "review.consistency", "consistency_review", "progress",
                  "一致性审校完成", f"发现 {count} 处不一致")

    async def _stage_finalize(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            open_issues = (
                session.query(Issue)
                .filter(Issue.run_id == run_id, Issue.status == "open",
                        Issue.severity.in_(["critical", "major"]))
                .all()
            )
            by_segment: dict[str, list[Issue]] = {}
            for issue in open_issues:
                if issue.segment_id:
                    by_segment.setdefault(issue.segment_id, []).append(issue)
            segments = {s.id: s for s in run.segments}
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id).order_by(DocumentGlossary.version.desc()).first()
            )
            glossary = glossary_row.entries if glossary_row else []
        if not by_segment:
            with SessionLocal() as session:
                for seg in session.query(Segment).filter_by(run_id=run_id).all():
                    if seg.status != "final":
                        seg.versions = {**seg.versions, "reviewed": seg.translation,
                                        "final": seg.translation}
                        seg.status = "final"
                session.commit()
            self.emit(run_id, "finalize.noop", "finalize", "progress",
                      "定稿完成", "无需修改，直接定稿")
            return
        for seg_id, issues in by_segment.items():
            if self._is_cancelled(run_id):
                return
            seg = segments[seg_id]
            result = await llm.call_role(
                tofu=self.tofu, settings=self.settings, role="finalizer",
                prompt_name="finalize",
                variables={
                    "glossary": glossary,
                    "source": seg.source,
                    "translation": seg.translation,
                    "issues": [
                        {"severity": i.severity, "category": i.category,
                         "message": i.message, "suggested_fix": i.suggested_fix}
                        for i in issues
                    ],
                },
                schema_name="finalize", model=self.settings.translator_model, run_id=run_id,
            )
            final_text = result["final_translation"]
            with SessionLocal() as session:
                seg_row = session.get(Segment, seg_id)
                seg_row.versions = {**seg_row.versions, "reviewed": seg_row.translation,
                                    "final": final_text}
                seg_row.translation = final_text
                seg_row.status = "final"
                for issue in session.query(Issue).filter(
                    Issue.id.in_([i.id for i in issues])
                ).all():
                    issue.status = "resolved"
                session.commit()
            self.emit(run_id, "finalize.segment", "finalize", "progress",
                      f"句段 {seg.idx + 1} 已定稿",
                      f"解决 {len(issues)} 个问题，{len(result.get('changes', []))} 处修改",
                      segment_ids=[seg_id],
                      metrics={"changes": result.get("changes", [])})

    async def _stage_final_qa(self, run_id: str) -> None:
        """Release gate (§26): critical>0 -> loop back or WAITING_HUMAN_REVIEW."""
        self._replace_issues(run_id, reviewer="final_qa")
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            glossary_row = (
                session.query(DocumentGlossary)
                .filter_by(run_id=run_id).order_by(DocumentGlossary.version.desc()).first()
            )
            glossary = glossary_row.entries if glossary_row else []
            segments = [(s.id, s.source, s.translation) for s in run.segments]
        critical = 0
        for seg_id, source, translation in segments:
            if not translation:
                self._add_issue(run_id, seg_id, "final_qa", {
                    "severity": "critical", "category": "completeness",
                    "source_span": source[:50], "target_span": "",
                    "message": "句段缺少译文", "suggested_fix": "补译",
                })
                critical += 1
                continue
            for finding in validators.run_deterministic(source, translation, glossary):
                self._add_issue(run_id, seg_id, "final_qa", finding)
                if finding["severity"] == "critical":
                    critical += 1
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if critical > 0:
                if run.loop_count < self.settings.max_finalize_loops:
                    run.loop_count += 1
                    run.current_stage = "finalize"  # loop back
                    run.status = RunStatus.FINALIZING
                    decision = f"发现 {critical} 个严重问题，回到定稿阶段（第 {run.loop_count} 轮）"
                else:
                    run.status = RunStatus.WAITING_HUMAN_REVIEW
                    run.current_stage = "final_qa"
                    decision = f"超过定稿循环上限，仍有 {critical} 个严重问题，等待人工审校"
            else:
                run.current_stage = "complete"
                decision = "终审通过"
            waiting = run.status == RunStatus.WAITING_HUMAN_REVIEW
            session.commit()
        if waiting:
            self.emit(run_id, "run.waiting_human", "final_qa", "failed",
                      "等待人工审校", decision)
        self.emit(run_id, "qa.final", "final_qa", "progress", "终审 QA", decision,
                  metrics={"critical_open": critical})

    async def _stage_complete(self, run_id: str) -> None:
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            if run.status == RunStatus.WAITING_HUMAN_REVIEW:
                self.emit(run_id, "run.waiting_human", "complete", "failed",
                          "等待人工审校", "终审仍存在严重问题")
                return
            run.status = RunStatus.COMPLETED
            run.progress = 1.0
            session.commit()
        self.emit(run_id, "run.completed", "complete", "completed", "翻译完成",
                  "全部阶段执行完毕", progress=1.0)

    # ----------------------------------------------------------------- helpers
    def _replace_issues(self, run_id: str, *, reviewer: str) -> None:
        """Idempotent review: a reviewer re-run replaces its own open issues."""
        with SessionLocal() as session:
            session.query(Issue).filter(
                Issue.run_id == run_id, Issue.reviewer == reviewer, Issue.status == "open"
            ).delete(synchronize_session=False)
            session.commit()

    def _add_issue(self, run_id: str, segment_id: str | None, reviewer: str, finding: dict) -> None:
        with SessionLocal() as session:
            session.add(Issue(
                id=uuid.uuid4().hex, run_id=run_id, segment_id=segment_id, reviewer=reviewer,
                severity=finding.get("severity", "minor"),
                category=finding.get("category", "other"),
                source_span=finding.get("source_span", ""),
                target_span=finding.get("target_span", ""),
                message=finding.get("message", ""),
                suggested_fix=finding.get("suggested_fix"),
                evidence_refs=finding.get("evidence_refs", []),
            ))
            session.commit()
