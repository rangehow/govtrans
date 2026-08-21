"""Benchmark runner (§36/§37) + single-pass baseline (§38).

run_benchmark(): drives the REAL orchestrator over a gold set and computes
deterministic metrics. run_baseline(): single direct translator call with
no retrieval/glossary/QA — the never-deleted comparison point.
"""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy import func

from apps.api.config import Settings
from apps.api.db import SessionLocal
from agents.roles import llm
from evaluation.gold_set import GoldSet, load_gold_set
from evaluation.metrics import aggregate, chrf, numbers_score, term_score
from evaluation.models import BenchmarkRun
from services.orchestrator.engine import Orchestrator
from services.orchestrator.models import Issue, ModelUsage, TranslationRun

logger = logging.getLogger("govtrans.evaluation")

BASELINE_NAME = "single-pass-baseline"


def _score_item(source: str, reference: str, hypothesis: str, glossary: list[dict]) -> dict:
    return {
        "chrf": chrf(hypothesis, reference),
        "numbers": numbers_score(source, hypothesis),
        "terminology": term_score(source, hypothesis, glossary),
    }


async def run_benchmark(
    orch: Orchestrator, *, gold_set_name: str, name: str | None = None,
    corpus_version: str | None = None,
) -> str:
    """Full-pipeline benchmark. Returns BenchmarkRun id."""
    gold: GoldSet = load_gold_set(gold_set_name)
    settings = orch.settings
    with SessionLocal() as session:
        bench = BenchmarkRun(
            name=name or f"benchmark-{gold_set_name}",
            kind="pipeline", gold_set=gold_set_name,
            model=settings.translator_model,
            pipeline_version=settings.pipeline_version, corpus_version=corpus_version,
        )
        session.add(bench)
        session.commit()
        bench_id = bench.id
    try:
        item_metrics: list[dict] = []
        started = time.monotonic()
        for item in gold.items:
            run_id = orch.create_run(
                source_text=item.source, confidentiality="PUBLIC",
                document_type=item.document_type,
            )
            await orch.execute(run_id)
            with SessionLocal() as session:
                run = session.get(TranslationRun, run_id)
                hypothesis = " ".join(
                    s.translation or "" for s in run.segments
                )
                mqm = {"critical": 0, "major": 0, "minor": 0}
                for issue in session.query(Issue).filter_by(run_id=run_id).all():
                    if issue.severity in mqm:
                        mqm[issue.severity] += 1
            metrics = _score_item(item.source, item.reference, hypothesis, item.glossary)
            metrics["mqm_critical"] = mqm["critical"]
            metrics["mqm_major"] = mqm["major"]
            item_metrics.append(metrics)
        result = aggregate(item_metrics)
        result["items"] = len(item_metrics)
        result["latency_ms"] = int((time.monotonic() - started) * 1000)
        result["gold_set_version"] = gold.version
        with SessionLocal() as session:
            bench = session.get(BenchmarkRun, bench_id)
            bench.metrics = result
            bench.status = "completed"
            session.commit()
    except Exception as exc:
        with SessionLocal() as session:
            bench = session.get(BenchmarkRun, bench_id)
            bench.status = "failed"
            bench.error = f"{type(exc).__name__}: {exc}"[:1000]
            session.commit()
        raise
    return bench_id


async def run_baseline(settings: Settings, tofu, *, gold_set_name: str) -> str:
    """§38 baseline: single-pass LLM translation, no retrieval/glossary/QA.
    Persists as kind='baseline'. Never delete these rows."""
    gold = load_gold_set(gold_set_name)
    with SessionLocal() as session:
        bench = BenchmarkRun(
            name=BASELINE_NAME, kind="baseline", gold_set=gold_set_name,
            model=settings.translator_model, pipeline_version=settings.pipeline_version,
        )
        session.add(bench)
        session.commit()
        bench_id = bench.id
    item_metrics: list[dict] = []
    started = time.monotonic()
    for item in gold.items:
        result: dict[str, Any] = await llm.call_role(
            tofu=tofu, settings=settings, role="baseline_translator",
            prompt_name="baseline_translate", variables={"source_text": item.source},
            schema_name="baseline", model=settings.translator_model, run_id=None,
        )
        hypothesis = result["translation"]
        # score against the SAME glossary as the pipeline — an empty glossary
        # here would trivially inflate the baseline's terminology score
        item_metrics.append(_score_item(item.source, item.reference, hypothesis, item.glossary))
    metrics = aggregate(item_metrics)
    metrics["items"] = len(item_metrics)
    metrics["latency_ms"] = int((time.monotonic() - started) * 1000)
    with SessionLocal() as session:
        bench = session.get(BenchmarkRun, bench_id)
        bench.metrics = metrics
        bench.status = "completed"
        session.commit()
    return bench_id


def compare_to_baseline(bench_id: str) -> dict:
    """Regression gate (§37): a pipeline run must not fall below baseline."""
    with SessionLocal() as session:
        bench = session.get(BenchmarkRun, bench_id)
        if not bench or bench.status != "completed":
            raise ValueError("benchmark not completed")
        baseline = (
            session.query(BenchmarkRun)
            .filter_by(kind="baseline", gold_set=bench.gold_set, status="completed")
            .order_by(BenchmarkRun.created_at.desc())
            .first()
        )
        if not baseline:
            return {"baseline": None, "verdict": "no_baseline"}
        deltas = {}
        for key in ("chrf", "numbers", "terminology"):
            cur = bench.metrics.get(key)
            base = baseline.metrics.get(key)
            if cur is not None and base is not None:
                deltas[key] = round(cur - base, 4)
        verdict = "pass" if all(d >= -0.01 for d in deltas.values()) else "regression"
        return {"baseline": baseline.id, "deltas": deltas, "verdict": verdict}
