"""Run endpoints. SSE contract (§10): GET /api/runs/{id}/events replays
persisted events after ?cursor=N (or Last-Event-ID) then follows live.
"""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse
from sqlalchemy import func

from apps.api.db import SessionLocal
from apps.api.deps import get_orchestrator
from services.orchestrator.engine import Orchestrator
from services.orchestrator.models import (
    Issue,
    ModelUsage,
    RunEvent,
    RunStatus,
    TranslationRun,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


class CreateRunRequest(BaseModel):
    source_text: str = Field(min_length=1, max_length=100_000)
    direction: str = Field(default="zh-en", pattern="^(zh-en|en-zh)$")
    confidentiality: str = Field(default="PUBLIC", pattern="^(PUBLIC|INTERNAL|CONFIDENTIAL)$")
    document_type: str | None = None


@router.post("", status_code=202)
async def create_run(body: CreateRunRequest, orch: Orchestrator = Depends(get_orchestrator)):
    if body.direction != "zh-en":
        raise HTTPException(400, "当前版本仅支持 zh-en；en-zh 为架构预留")
    run_id = orch.create_run(
        source_text=body.source_text,
        confidentiality=body.confidentiality,
        document_type=body.document_type,
        direction=body.direction,
    )
    orch.start(run_id)
    return {"run_id": run_id, "status": RunStatus.CREATED}


def _run_detail(run: TranslationRun) -> dict:
    return {
        "run_id": run.id,
        "status": run.status,
        "progress": run.progress,
        "error": run.error,
        "summary": run.summary,
        "document_type": run.document_type,
        "confidentiality": run.confidentiality,
        "created_at": run.created_at.isoformat() if run.created_at else None,
        "segments": [
            {"id": s.id, "idx": s.idx, "source": s.source,
             "translation": s.translation, "status": s.status, "versions": s.versions}
            for s in run.segments
        ],
        "issues": [
            {"id": i.id, "segment_id": i.segment_id, "reviewer": i.reviewer,
             "severity": i.severity, "category": i.category, "message": i.message,
             "suggested_fix": i.suggested_fix, "status": i.status}
            for i in run.issues
        ],
    }


@router.get("/{run_id}")
def get_run(run_id: str):
    with SessionLocal() as session:
        run = session.get(TranslationRun, run_id)
        if not run:
            raise HTTPException(404, "run not found")
        return _run_detail(run)


@router.post("/{run_id}/cancel")
def cancel_run(run_id: str, orch: Orchestrator = Depends(get_orchestrator)):
    if not orch.cancel(run_id):
        raise HTTPException(409, "run not found or already terminal")
    return {"status": "CANCELLED"}


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
        role: {"calls": calls, "input_tokens": int(inp or 0), "output_tokens": int(out or 0),
               "latency_ms": int(lat or 0)}
        for role, calls, inp, out, lat in rows
    }
    total = {
        "calls": sum(r["calls"] for r in by_role.values()),
        "input_tokens": sum(r["input_tokens"] for r in by_role.values()),
        "output_tokens": sum(r["output_tokens"] for r in by_role.values()),
    }
    return {"total": total, "by_role": by_role}


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
                replayed = [
                    {
                        "id": r.id, "run_id": r.run_id, "seq": r.seq, "type": r.type,
                        "phase": r.phase, "status": r.status, "title": r.title,
                        "summary": r.summary, "progress": r.progress,
                        "segment_ids": r.segment_ids, "evidence": r.evidence,
                        "metrics": r.metrics,
                        "created_at": r.created_at.isoformat() if r.created_at else None,
                    }
                    for r in rows
                ]
            for ev in replayed:
                yield {"id": str(ev["seq"]), "data": json.dumps(ev, ensure_ascii=False)}
            last = replayed[-1]["seq"] if replayed else cursor
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
        finally:
            orch.unsubscribe(run_id, queue)

    return EventSourceResponse(stream())
