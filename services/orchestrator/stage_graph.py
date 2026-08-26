"""Deterministic Stage Graph (§9, AD-04). The orchestrator walks this list —
agents never plan the run themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.orchestrator.models import RunStatus


@dataclass(frozen=True)
class Stage:
    id: str
    title: str
    run_status: str  # run lifecycle status while this stage executes


STAGES: list[Stage] = [
    Stage("parse", "解析文档", RunStatus.PARSING),
    Stage("analyze", "分析文档", RunStatus.ANALYZING),
    Stage("terminology", "研究术语", RunStatus.RESEARCHING),
    Stage("retrieve", "准备参考资料", RunStatus.RESEARCHING),
    Stage("plan", "生成文档术语表", RunStatus.RESEARCHING),
    Stage("translate", "翻译", RunStatus.TRANSLATING),
    Stage("deterministic_qa", "确定性 QA", RunStatus.QA),
    Stage("term_review", "术语审校", RunStatus.REVIEWING),
    Stage("semantic_review", "语义审校", RunStatus.REVIEWING),
    Stage("style_review", "风格审校", RunStatus.REVIEWING),
    Stage("consistency_review", "一致性审校", RunStatus.REVIEWING),
    Stage("finalize", "定稿", RunStatus.FINALIZING),
    Stage("final_qa", "终审 QA", RunStatus.QA),
    Stage("complete", "完成", RunStatus.COMPLETED),
]

STAGE_INDEX = {stage.id: i for i, stage in enumerate(STAGES)}

ROLE_TO_STAGE = {
    "analyst": "analyze",
    "term_extractor": "terminology",
    "translator": "translate",
    "semantic_reviewer": "semantic_review",
    "style_reviewer": "style_review",
    "consistency_reviewer": "consistency_review",
    "finalizer": "finalize",
    "finalizer_escalated": "finalize",
    "final_release_reviewer": "final_qa",
}


def stage_runtime_spec(stage_id: str, settings: Any, *, loop_count: int = 0) -> dict:
    """Describe the real execution boundary behind one visible stage.

    The same metadata is persisted on stage events and returned in run detail,
    so the UI never has to guess model names from marketing labels.
    """
    specs = {
        "parse": ("rules", [], [], "结构解析器"),
        "analyze": ("model", ["analyst"], [settings.fast_model], "大模型"),
        "terminology": (
            "hybrid", ["term_extractor"], [settings.fast_model], "术语抽取 + 官方核验",
        ),
        "retrieve": ("rules", [], [], "BM25 + 语料检索"),
        "plan": ("rules", [], [], "术语约束引擎"),
        "translate": (
            "model", ["translator"], [settings.translator_model], "大模型",
        ),
        "deterministic_qa": ("rules", [], [], "确定性规则"),
        "term_review": ("rules", [], [], "术语规则"),
        "semantic_review": (
            "model", ["semantic_reviewer"], [settings.review_model], "并行大模型审校",
        ),
        "style_review": (
            "model", ["style_reviewer"], [settings.review_model], "并行大模型审校",
        ),
        "consistency_review": (
            "hybrid", ["consistency_reviewer"], [settings.review_model], "并行规则 + 全文大模型",
        ),
        "finalize": (
            "model",
            ["finalizer_escalated" if loop_count > 0 else "finalizer"],
            [settings.review_model if loop_count > 0 else settings.translator_model],
            "定向修订模型",
        ),
        "final_qa": (
            "rules", [], [], "确定性发布闸门",
        ),
        "complete": ("rules", [], [], "交付归档器"),
    }
    kind, roles, models, engine = specs[stage_id]
    return {
        "kind": kind,
        "roles": roles,
        "models": models,
        "engine": engine,
    }

# Stages that loop back when final_qa still finds critical issues (§26).
FINALIZE_LOOP_BACK = "finalize"
