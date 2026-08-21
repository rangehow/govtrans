import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from apps.api.db import SessionLocal
from apps.api.deps import get_orchestrator
from evaluation.models import BenchmarkRun
from evaluation.runner import compare_to_baseline, run_baseline, run_benchmark
from services.orchestrator.engine import Orchestrator

router = APIRouter(prefix="/api/benchmarks", tags=["evaluation"])


@router.get("")
def list_benchmarks():
    with SessionLocal() as session:
        rows = session.query(BenchmarkRun).order_by(BenchmarkRun.created_at.desc()).limit(100).all()
        return {"benchmarks": [
            {"id": b.id, "name": b.name, "kind": b.kind, "gold_set": b.gold_set,
             "model": b.model, "pipeline_version": b.pipeline_version,
             "status": b.status, "metrics": b.metrics, "error": b.error,
             "created_at": b.created_at.isoformat()}
            for b in rows
        ]}


class RunBenchmarkRequest(BaseModel):
    gold_set: str = "seed_gov_zh_en"
    name: str | None = None
    kind: str = "pipeline"  # pipeline | baseline


@router.post("", status_code=202)
async def start_benchmark(body: RunBenchmarkRequest, orch: Orchestrator = Depends(get_orchestrator)):
    if body.kind == "baseline":
        bench_id = await run_baseline(orch.settings, orch.tofu, gold_set_name=body.gold_set)
    elif body.kind == "pipeline":
        bench_id = await run_benchmark(orch, gold_set_name=body.gold_set, name=body.name)
    else:
        raise HTTPException(400, "kind must be pipeline|baseline")
    return {"benchmark_id": bench_id}


@router.get("/{bench_id}")
def get_benchmark(bench_id: str):
    with SessionLocal() as session:
        bench = session.get(BenchmarkRun, bench_id)
        if not bench:
            raise HTTPException(404, "benchmark not found")
        comparison = None
        if bench.kind == "pipeline" and bench.status == "completed":
            comparison = compare_to_baseline(bench_id)
        return {
            "id": bench.id, "name": bench.name, "kind": bench.kind,
            "gold_set": bench.gold_set, "model": bench.model,
            "pipeline_version": bench.pipeline_version, "status": bench.status,
            "metrics": bench.metrics, "error": bench.error,
            "comparison": comparison,
        }
