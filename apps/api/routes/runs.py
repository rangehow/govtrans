"""Run endpoints. SSE contract (§10): GET /api/runs/{id}/events replays
persisted events after ?cursor=N (or Last-Event-ID) then follows live.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import case, func

from apps.api.config import get_settings
from apps.api.db import SessionLocal
from apps.api.deps import get_orchestrator
from services.languages import language_pair_payload, resolve_language_pair
from services.orchestrator.engine import Orchestrator
from services.orchestrator.models import (
    Issue,
    ModelUsage,
    DocumentGlossary,
    RunEvent,
    RunStatus,
    Segment,
    TranslationRun,
)
from services.orchestrator.skills import SKILL_LABELS, base_skills_for
from services.orchestrator.segmentation import infer_block_kind
from services.orchestrator.stage_graph import ROLE_TO_STAGE, STAGES, stage_runtime_spec

router = APIRouter(prefix="/api/runs", tags=["runs"])

QUALITY_PENALTIES = {"critical": 30, "major": 8, "minor": 2}


class ManualTermRequest(BaseModel):
    source: str = Field(min_length=1, max_length=200)
    target: str = Field(min_length=1, max_length=500)
    proper_name: bool = False
    note: str | None = Field(default=None, max_length=500)


class CreateRunRequest(BaseModel):
    source_text: str = Field(min_length=1, max_length=100_000)
    source_language: str | None = None
    target_language: str | None = None
    # Accepted for older clients. New clients should send the two language fields.
    direction: str | None = None
    confidentiality: str = Field(default="PUBLIC", pattern="^(PUBLIC|INTERNAL|CONFIDENTIAL)$")
    document_type: str | None = None
    style_skills: list[str] | None = None
    manual_terms: list[ManualTermRequest] = Field(default_factory=list, max_length=100)
    translation_mode: str = Field(default="coherent", pattern="^(coherent|balanced)$")


class SegmentEditRequest(BaseModel):
    translation: str = Field(min_length=1, max_length=100_000)
    resolve_issue_id: str | None = Field(default=None, max_length=32)


@router.post("", status_code=202)
async def create_run(body: CreateRunRequest, orch: Orchestrator = Depends(get_orchestrator)):
    try:
        source_language, target_language = resolve_language_pair(
            body.source_language,
            body.target_language,
            body.direction,
        )
        run_id = orch.create_run(
            source_text=body.source_text,
            confidentiality=body.confidentiality,
            document_type=body.document_type,
            source_language=source_language,
            target_language=target_language,
            style_skills=body.style_skills,
            manual_terms=[term.model_dump() for term in body.manual_terms],
            translation_mode=body.translation_mode,
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    orch.start(run_id)
    return {
        "run_id": run_id,
        "status": RunStatus.CREATED,
        "language_pair": language_pair_payload(source_language, target_language),
    }


def _score_from_counts(open_counts: dict[str, int]) -> int:
    return max(
        0,
        100
        - sum(
            open_counts.get(severity, 0) * penalty
            for severity, penalty in QUALITY_PENALTIES.items()
        ),
    )


def _quality_detail(run: TranslationRun, open_counts: dict[str, int]) -> dict:
    blocking = open_counts["critical"] + open_counts["major"]
    advisory = open_counts["minor"]
    score = _score_from_counts(open_counts)
    if run.status == RunStatus.COMPLETED:
        gate = "passed"
        label = "已通过交付检查"
    elif run.status == RunStatus.QUALITY_GATE_FAILED:
        gate = "needs_optimization"
        label = "还需继续优化"
    elif run.status in {
        RunStatus.FAILED,
        RunStatus.CANCELLED,
        RunStatus.WAITING_HUMAN_REVIEW,
    }:
        gate = "interrupted"
        label = "检查未完成"
    else:
        gate = "checking"
        label = "正在动态检查"
    pins = run.version_pins or {}
    revision_rounds = int(pins.get("previous_revision_rounds") or 0) + int(run.loop_count or 0)
    return {
        "score": score,
        "open": open_counts,
        "deductions": {
            severity: open_counts[severity] * penalty
            for severity, penalty in QUALITY_PENALTIES.items()
        },
        "blocking": blocking,
        "advisory": advisory,
        "gate": gate,
        "label": label,
        "revision_rounds": revision_rounds,
        "max_auto_rounds": get_settings().max_finalize_loops,
        "continue_count": int(pins.get("manual_continue_count") or 0),
        "score_basis": (
            "自动质检分是全文当前未解决检查项的规则化摘要，不是模型对译文的主观打分。"
            "100 分起：严重项每项扣 30 分、重要项扣 8 分、轻微建议扣 2 分。"
        ),
        "release_rule": (
            "交付条件是严重和重要项全部清零。轻微项是不影响忠实性与可用性的润色建议，"
            "会保留作为透明审计，不会为追求数字 100 而反复改写。"
        ),
    }


def _pipeline_detail(run: TranslationRun, usage_rows: list[ModelUsage]) -> list[dict]:
    """Return configured and observed execution details for every stage."""
    settings = get_settings()
    usage_by_stage: dict[str, list[ModelUsage]] = {stage.id: [] for stage in STAGES}
    for usage in usage_rows:
        stage_id = ROLE_TO_STAGE.get(usage.role)
        if stage_id:
            usage_by_stage[stage_id].append(usage)

    result: list[dict] = []
    for stage in STAGES:
        spec = stage_runtime_spec(stage.id, settings, loop_count=int(run.loop_count or 0))
        calls = sorted(
            usage_by_stage[stage.id],
            key=lambda item: (item.created_at.isoformat() if item.created_at else "", item.id),
        )
        result.append(
            {
                "id": stage.id,
                "title": stage.title,
                "kind": spec["kind"],
                "engine": spec["engine"],
                "roles": list(
                    dict.fromkeys(
                        [
                            *spec["roles"],
                            *(item.role for item in calls),
                        ]
                    )
                ),
                "models": list(
                    dict.fromkeys(
                        [
                            *spec["models"],
                            *(item.model for item in calls),
                        ]
                    )
                ),
                "calls": len(calls),
                "latency_ms": sum(item.latency_ms or 0 for item in calls),
                "retries": sum(item.retries or 0 for item in calls),
                "last_status": calls[-1].status if calls else None,
                "call_details": [
                    {
                        "role": item.role,
                        "model": item.model,
                        "latency_ms": item.latency_ms,
                        "retries": item.retries,
                        "status": item.status,
                        "created_at": item.created_at.isoformat() if item.created_at else None,
                    }
                    for item in calls
                ],
            }
        )
    return result


def _run_detail(
    run: TranslationRun,
    glossary_entries: list[dict] | None = None,
    model_usage: list[ModelUsage] | None = None,
) -> dict:
    open_counts = {"critical": 0, "major": 0, "minor": 0}
    for issue in run.issues:
        if issue.status == "open" and issue.severity in open_counts:
            open_counts[issue.severity] += 1
    pins = run.version_pins or {}
    references_by_segment = pins.get("references", {})
    if not isinstance(references_by_segment, dict):
        references_by_segment = {}
    all_references = [
        reference
        for segment_references in references_by_segment.values()
        if isinstance(segment_references, list)
        for reference in segment_references
        if isinstance(reference, dict)
    ]
    selected_styles = list(run.style_skills or [])
    foundation_skills = base_skills_for(run.source_language, run.target_language)
    runtime_skills = [
        {
            "id": skill_id,
            "name": SKILL_LABELS.get(skill_id, skill_id),
            "kind": "foundation" if skill_id in foundation_skills else "style",
            "selection": (
                "always"
                if skill_id in foundation_skills
                else "automatic"
                if pins.get("style_auto")
                else "manual"
            ),
        }
        for skill_id in [*foundation_skills, *selected_styles]
    ]
    return {
        "run_id": run.id,
        "status": run.status,
        "direction": run.direction,
        "source_language": run.source_language,
        "target_language": run.target_language,
        "language_pair": language_pair_payload(run.source_language, run.target_language),
        "progress": run.progress,
        "error": run.error,
        "summary": run.summary,
        "source_text": run.source_text,
        "document_type": run.document_type,
        "confidentiality": run.confidentiality,
        "style_skills": run.style_skills,
        "manual_terms": run.manual_terms,
        "translation_mode": run.translation_mode,
        "current_stage": run.current_stage,
        "loop_count": run.loop_count,
        "pipeline_version": run.pipeline_version,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "updated_at": run.updated_at.isoformat() if run.updated_at else None,
        "quality": _quality_detail(run, open_counts),
        "pipeline_steps": _pipeline_detail(run, model_usage or []),
        "knowledge_usage": {
            "style_skills": runtime_skills,
            "terminology": glossary_entries or [],
            "references_by_segment": references_by_segment,
            "reference_count": len(all_references),
            "automatic_reference_count": sum(
                item.get("kind") == "official_corpus" for item in all_references
            ),
            "verified_reference_count": sum(
                item.get("kind") == "verified_memory" for item in all_references
            ),
        },
        "segments": [
            {
                "id": s.id,
                "idx": s.idx,
                "source": s.source,
                "translation": s.translation,
                "status": s.status,
                "versions": s.versions,
                "kind": infer_block_kind(s.source, index=s.idx),
            }
            for s in run.segments
        ],
        "issues": [
            {
                "id": i.id,
                "segment_id": i.segment_id,
                "reviewer": i.reviewer,
                "severity": i.severity,
                "category": i.category,
                "message": i.message,
                "source_span": i.source_span,
                "target_span": i.target_span,
                "suggested_fix": i.suggested_fix,
                "status": i.status,
            }
            for i in run.issues
        ],
    }


def _event_detail(event: RunEvent) -> dict:
    return {
        "id": event.id,
        "run_id": event.run_id,
        "seq": event.seq,
        "type": event.type,
        "phase": event.phase,
        "status": event.status,
        "title": event.title,
        "summary": event.summary,
        "progress": event.progress,
        "segment_ids": event.segment_ids,
        "evidence": event.evidence,
        "metrics": event.metrics,
        "created_at": event.created_at.isoformat() if event.created_at else None,
    }


def _source_label(source_text: str) -> tuple[str, str]:
    compact = re.sub(r"\s+", " ", source_text).strip()
    first_line = next((line.strip() for line in source_text.splitlines() if line.strip()), compact)
    title = first_line[:72] + ("…" if len(first_line) > 72 else "")
    preview = compact[:140] + ("…" if len(compact) > 140 else "")
    return title or "未命名翻译", preview


@router.get("")
def list_runs(
    limit: int = Query(default=30, ge=1, le=100),
    status: str | None = Query(default=None),
):
    """Return lightweight run metadata; transcript bodies stay in detail reads."""
    # Each aggregate is isolated before joining so segment and issue counts do
    # not multiply one another. This mirrors ChatUI's metadata-list/detail-read
    # split and keeps refresh recovery cheap even for long documents.
    with SessionLocal() as session:
        segment_stats = (
            session.query(
                Segment.run_id.label("run_id"),
                func.count(Segment.id).label("segment_count"),
                func.sum(case((Segment.status == "final", 1), else_=0)).label("final_count"),
            )
            .group_by(Segment.run_id)
            .subquery()
        )
        issue_stats = (
            session.query(
                Issue.run_id.label("run_id"),
                func.sum(
                    case(((Issue.status == "open") & (Issue.severity == "critical"), 1), else_=0)
                ).label("critical_count"),
                func.sum(
                    case(((Issue.status == "open") & (Issue.severity == "major"), 1), else_=0)
                ).label("major_count"),
                func.sum(
                    case(((Issue.status == "open") & (Issue.severity == "minor"), 1), else_=0)
                ).label("minor_count"),
            )
            .group_by(Issue.run_id)
            .subquery()
        )
        query = (
            session.query(
                TranslationRun,
                func.coalesce(segment_stats.c.segment_count, 0),
                func.coalesce(segment_stats.c.final_count, 0),
                func.coalesce(issue_stats.c.critical_count, 0),
                func.coalesce(issue_stats.c.major_count, 0),
                func.coalesce(issue_stats.c.minor_count, 0),
            )
            .outerjoin(segment_stats, segment_stats.c.run_id == TranslationRun.id)
            .outerjoin(issue_stats, issue_stats.c.run_id == TranslationRun.id)
        )
        if status:
            query = query.filter(TranslationRun.status == status)
        rows = (
            query.order_by(TranslationRun.updated_at.desc(), TranslationRun.id.desc())
            .limit(limit + 1)
            .all()
        )

    has_more = len(rows) > limit
    items = []
    for run, segment_count, final_count, critical, major, minor in rows[:limit]:
        title, preview = _source_label(run.source_text)
        counts = {"critical": int(critical), "major": int(major), "minor": int(minor)}
        items.append(
            {
                "run_id": run.id,
                "title": title,
                "source_preview": preview,
                "status": run.status,
                "direction": run.direction,
                "source_language": run.source_language,
                "target_language": run.target_language,
                "progress": run.progress,
                "document_type": run.document_type,
                "confidentiality": run.confidentiality,
                "segment_count": int(segment_count),
                "final_segment_count": int(final_count),
                "open_issues": counts,
                "quality_score": _score_from_counts(counts),
                "created_at": run.created_at.isoformat() if run.created_at else None,
                "updated_at": run.updated_at.isoformat() if run.updated_at else None,
            }
        )
    return {"runs": items, "has_more": has_more}


@router.get("/{run_id}")
def get_run(run_id: str):
    with SessionLocal() as session:
        run = session.get(TranslationRun, run_id)
        if not run:
            raise HTTPException(404, "run not found")
        glossary = (
            session.query(DocumentGlossary)
            .filter_by(run_id=run_id)
            .order_by(DocumentGlossary.version.desc())
            .first()
        )
        model_usage = (
            session.query(ModelUsage)
            .filter_by(run_id=run_id)
            .order_by(ModelUsage.created_at, ModelUsage.id)
            .all()
        )
        return _run_detail(run, glossary.entries if glossary else [], model_usage)


@router.patch("/{run_id}/segments/{segment_id}")
def update_segment_translation(
    run_id: str,
    segment_id: str,
    body: SegmentEditRequest,
    orch: Orchestrator = Depends(get_orchestrator),
):
    """Persist a deliberate user edit and optionally resolve its QA item.

    Active model work is rejected so a background finalizer can never silently
    overwrite the user's text. Paused and completed runs remain editable.
    """
    translation = body.translation.strip()
    if not translation:
        raise HTTPException(422, "译文不能为空")

    with SessionLocal() as session:
        run = session.get(TranslationRun, run_id)
        if not run:
            raise HTTPException(404, "run not found")
        if run.status in RunStatus.ACTIVE:
            raise HTTPException(409, "任务仍在自动处理，请等待本轮结束后再手动修改")
        segment = session.get(Segment, segment_id)
        if not segment or segment.run_id != run_id:
            raise HTTPException(404, "segment not found")

        previous = segment.translation or ""
        versions = dict(segment.versions or {})
        if previous and previous != translation:
            versions["manual_previous"] = previous
        versions["manual"] = translation
        versions["final"] = translation
        segment.translation = translation
        segment.versions = versions
        segment.status = "final"
        segment_index = segment.idx

        resolved_issue: Issue | None = None
        if body.resolve_issue_id:
            resolved_issue = session.get(Issue, body.resolve_issue_id)
            if (
                not resolved_issue
                or resolved_issue.run_id != run_id
                or resolved_issue.segment_id != segment_id
            ):
                raise HTTPException(404, "issue not found for this segment")
            resolved_issue.status = "resolved"

        run.updated_at = datetime.now(timezone.utc)
        session.commit()
        issue_was_resolved = resolved_issue is not None

    orch.emit(
        run_id,
        "user.segment_edit",
        "manual_edit",
        "progress",
        f"第 {segment_index + 1} 段已人工修改",
        "译文已保存" + ("，对应质检项已标记解决" if issue_was_resolved else ""),
        segment_ids=[segment_id],
        metrics={
            "editor": "user",
            "resolved_issue_id": body.resolve_issue_id,
            "characters": len(translation),
        },
    )
    return get_run(run_id)


@router.get("/{run_id}/event-log")
def get_run_event_log(
    run_id: str,
    after: int = Query(default=0, ge=0),
    limit: int = Query(default=1_000, ge=1, le=2_000),
):
    with SessionLocal() as session:
        if not session.get(TranslationRun, run_id):
            raise HTTPException(404, "run not found")
        rows = (
            session.query(RunEvent)
            .filter(RunEvent.run_id == run_id, RunEvent.seq > after)
            .order_by(RunEvent.seq)
            .limit(limit)
            .all()
        )
        events = [_event_detail(row) for row in rows]
    return {"events": events, "last_cursor": events[-1]["seq"] if events else after}


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str, orch: Orchestrator = Depends(get_orchestrator)):
    if not orch.cancel(run_id):
        raise HTTPException(409, "run not found or already terminal")
    return {"status": "CANCELLED"}


@router.post("/{run_id}/continue", status_code=202)
async def continue_run(run_id: str, orch: Orchestrator = Depends(get_orchestrator)):
    if not orch.continue_quality(run_id):
        raise HTTPException(409, "仅可继续未通过质量闸门或因技术错误中断的任务")
    # Keep the established response contract; clients immediately refresh the
    # persisted run to obtain the exact resumed stage/status.
    return {"run_id": run_id, "status": RunStatus.FINALIZING}


@router.get("/{run_id}/cost")
def run_cost(run_id: str):
    with SessionLocal() as session:
        rows = (
            session.query(
                ModelUsage.role,
                func.count(ModelUsage.id),
                func.sum(ModelUsage.input_tokens),
                func.sum(ModelUsage.output_tokens),
                func.sum(ModelUsage.latency_ms),
            )
            .filter(ModelUsage.run_id == run_id)
            .group_by(ModelUsage.role)
            .all()
        )
    by_role = {
        role: {
            "calls": calls,
            "input_tokens": int(inp or 0),
            "output_tokens": int(out or 0),
            "latency_ms": int(lat or 0),
        }
        for role, calls, inp, out, lat in rows
    }
    total = {
        "calls": sum(r["calls"] for r in by_role.values()),
        "input_tokens": sum(r["input_tokens"] for r in by_role.values()),
        "output_tokens": sum(r["output_tokens"] for r in by_role.values()),
    }
    return {"total": total, "by_role": by_role}


@router.get("/{run_id}/export")
def export_run_endpoint(run_id: str, format: str = "docx"):
    from fastapi.responses import Response

    from services.export.exporters import FORMATS, export_run

    if format not in FORMATS:
        raise HTTPException(400, f"format must be one of {FORMATS}")
    with SessionLocal() as session:
        run = session.get(TranslationRun, run_id)
        if not run:
            raise HTTPException(404, "run not found")
        if run.status != RunStatus.COMPLETED:
            raise HTTPException(409, "quality gate has not approved this run for export")
        run_dict = {
            "id": run.id,
            "direction": run.direction,
            "status": run.status,
            "source_language": run.source_language,
            "target_language": run.target_language,
            "summary": run.summary,
            "confidentiality": run.confidentiality,
            "pipeline_version": run.pipeline_version,
            "version_pins": run.version_pins,
        }
        segments = [
            {"idx": s.idx, "source": s.source, "translation": s.translation, "versions": s.versions}
            for s in run.segments
        ]
    result = export_run(run_dict, segments, format)
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={"Content-Disposition": f'attachment; filename="{result.filename}"'},
    )


@router.get("/{run_id}/events")
async def run_events(
    run_id: str,
    request: Request,
    cursor: int = 0,
    orch: Orchestrator = Depends(get_orchestrator),
):
    last_event_id = request.headers.get("last-event-id")
    if last_event_id and last_event_id.isdigit():
        cursor = max(cursor, int(last_event_id))

    with SessionLocal() as session:
        if not session.get(TranslationRun, run_id):
            raise HTTPException(404, "run not found")

    async def stream():
        queue = orch.subscribe(run_id)
        try:
            # 1) Replay persisted events after the cursor (restart-safe).
            with SessionLocal() as session:
                rows = (
                    session.query(RunEvent)
                    .filter(RunEvent.run_id == run_id, RunEvent.seq > cursor)
                    .order_by(RunEvent.seq)
                    .all()
                )
                replayed = [_event_detail(row) for row in rows]
                terminal = session.get(TranslationRun, run_id).status in RunStatus.TERMINAL
            for ev in replayed:
                yield {"id": str(ev["seq"]), "data": json.dumps(ev, ensure_ascii=False)}
            last = replayed[-1]["seq"] if replayed else cursor
            if terminal:
                return
            # 2) Follow the live queue; skip anything already replayed.
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=15.0)
                except asyncio.TimeoutError:
                    yield {"comment": "heartbeat"}
                    continue
                if event.seq <= last:
                    continue
                last = event.seq
                yield {"id": str(event.seq), "data": event.model_dump_json()}
                if event.type in {
                    "run.completed",
                    "run.failed",
                    "run.cancelled",
                    "run.quality_gate_failed",
                }:
                    return
        finally:
            orch.unsubscribe(run_id, queue)

    return EventSourceResponse(stream())
