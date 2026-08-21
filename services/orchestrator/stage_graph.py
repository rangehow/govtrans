"""Deterministic Stage Graph (§9, AD-04). The orchestrator walks this list —
agents never plan the run themselves.
"""
from __future__ import annotations

from dataclasses import dataclass

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

# Stages that loop back when final_qa still finds critical issues (§26).
FINALIZE_LOOP_BACK = "finalize"
