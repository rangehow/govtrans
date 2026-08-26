import asyncio

import pytest
from fastapi import HTTPException

from apps.api.db import SessionLocal
from apps.api.routes.runs import (
    SegmentEditRequest,
    continue_run,
    export_run_endpoint,
    get_run,
    get_run_event_log,
    list_runs,
    update_segment_translation,
)
from services.orchestrator.models import (
    DocumentGlossary,
    Issue,
    ModelUsage,
    RunEvent,
    RunStatus,
    Segment,
    TranslationRun,
)


pytestmark = pytest.mark.unit


def _create_persisted_run(status: str = RunStatus.TRANSLATING) -> str:
    with SessionLocal() as session:
        run = TranslationRun(
            source_text="持久化恢复专用测试文本。",
            confidentiality="PUBLIC",
            direction="zh-en",
            status=status,
            progress=0.5,
            current_stage="translate",
            pipeline_version="test",
            version_pins={},
        )
        session.add(run)
        session.flush()
        session.add(Segment(run_id=run.id, idx=0, source=run.source_text))
        session.add(RunEvent(
            run_id=run.id,
            seq=1,
            type="run.created",
            phase="run",
            status="started",
            title="运行已创建",
            segment_ids=[],
            evidence=[],
            metrics={},
        ))
        session.commit()
        return run.id


def test_history_list_is_lightweight_and_detail_restores_full_source():
    run_id = _create_persisted_run()
    history = list_runs(limit=100, status=None)
    item = next(item for item in history["runs"] if item["run_id"] == run_id)
    assert item["title"].startswith("持久化恢复")
    assert "source_text" not in item
    assert item["segment_count"] == 1

    detail = get_run(run_id)
    assert detail["source_text"] == "持久化恢复专用测试文本。"
    assert detail["segments"][0]["source"] == detail["source_text"]


def test_event_log_replays_after_refresh_cursor():
    run_id = _create_persisted_run()
    first = get_run_event_log(run_id, after=0, limit=100)
    assert [event["seq"] for event in first["events"]] == [1]
    assert first["last_cursor"] == 1
    assert get_run_event_log(run_id, after=1, limit=100)["events"] == []


def test_run_detail_explains_actual_style_terms_and_reference_usage():
    run_id = _create_persisted_run()
    with SessionLocal() as session:
        run = session.get(TranslationRun, run_id)
        segment = session.query(Segment).filter_by(run_id=run_id).one()
        run.style_skills = ["scio-white-paper-distilled"]
        run.version_pins = {
            "style_auto": True,
            "references": {
                segment.id: [{
                    "id": "reference-1",
                    "source": "推动高质量发展",
                    "target": "promote high-quality development",
                    "kind": "official_corpus",
                    "usage": "advisory",
                }],
            },
        }
        session.add(DocumentGlossary(
            run_id=run_id,
            version=1,
            entries=[{
                "source": "高质量发展",
                "target": "high-quality development",
                "mandatory": True,
                "origin": "manual_run",
            }],
        ))
        session.commit()

    usage = get_run(run_id)["knowledge_usage"]
    assert usage["style_skills"][-1]["selection"] == "automatic"
    assert usage["terminology"][0]["mandatory"] is True
    assert usage["reference_count"] == 1
    assert usage["automatic_reference_count"] == 1
    assert usage["verified_reference_count"] == 0


def test_unapproved_run_cannot_be_exported():
    run_id = _create_persisted_run(RunStatus.QUALITY_GATE_FAILED)
    with pytest.raises(HTTPException) as caught:
        export_run_endpoint(run_id, format="txt")
    assert caught.value.status_code == 409


def test_quality_detail_explains_score_and_release_rule():
    run_id = _create_persisted_run(RunStatus.COMPLETED)
    with SessionLocal() as session:
        segment = session.query(Segment).filter_by(run_id=run_id).one()
        session.add(Issue(
            run_id=run_id,
            segment_id=segment.id,
            reviewer="style_reviewer",
            severity="minor",
            category="style",
            message="optional polish",
            status="open",
        ))
        session.commit()

    detail = get_run(run_id)
    quality = detail["quality"]
    assert quality["score"] == 98
    assert quality["gate"] == "passed"
    assert quality["blocking"] == 0
    assert quality["advisory"] == 1
    assert "不是模型对译文的主观打分" in quality["score_basis"]
    assert "轻微项" in quality["release_rule"]


def test_quality_gate_continue_endpoint_reopens_bounded_repair():
    class FakeOrchestrator:
        def __init__(self):
            self.requested = None

        def continue_quality(self, run_id):
            self.requested = run_id
            return True

    orchestrator = FakeOrchestrator()
    response = asyncio.run(continue_run("quality-paused-run", orch=orchestrator))
    assert orchestrator.requested == "quality-paused-run"
    assert response == {"run_id": "quality-paused-run", "status": RunStatus.FINALIZING}


def test_run_detail_exposes_pipeline_models_and_observed_latency():
    run_id = _create_persisted_run()
    with SessionLocal() as session:
        session.add(ModelUsage(
            run_id=run_id,
            role="analyst",
            model="test-fast-model",
            latency_ms=1234,
            retries=1,
            status="ok",
        ))
        session.commit()

    steps = {step["id"]: step for step in get_run(run_id)["pipeline_steps"]}
    assert steps["parse"]["models"] == []
    assert steps["parse"]["kind"] == "rules"
    assert "test-fast-model" in steps["analyze"]["models"]
    assert steps["analyze"]["calls"] == 1
    assert steps["analyze"]["latency_ms"] == 1234
    assert steps["analyze"]["retries"] == 1


def test_user_can_edit_terminal_segment_and_resolve_exact_issue():
    class FakeOrchestrator:
        def __init__(self):
            self.events = []

        def emit(self, *args, **kwargs):
            self.events.append((args, kwargs))

    run_id = _create_persisted_run(RunStatus.COMPLETED)
    with SessionLocal() as session:
        segment = session.query(Segment).filter_by(run_id=run_id).one()
        segment.translation = "Use the old term."
        segment.status = "final"
        issue = Issue(
            run_id=run_id,
            segment_id=segment.id,
            reviewer="style_reviewer",
            severity="minor",
            category="terminology_defects",
            source_span="旧术语",
            target_span="old term",
            message="Use the preferred term.",
            suggested_fix="preferred term",
            status="open",
        )
        session.add(issue)
        session.commit()
        segment_id = segment.id
        issue_id = issue.id

    orchestrator = FakeOrchestrator()
    detail = update_segment_translation(
        run_id,
        segment_id,
        SegmentEditRequest(
            translation="Use the preferred term.",
            resolve_issue_id=issue_id,
        ),
        orch=orchestrator,
    )

    assert detail["segments"][0]["translation"] == "Use the preferred term."
    edited_issue = next(item for item in detail["issues"] if item["id"] == issue_id)
    assert edited_issue["status"] == "resolved"
    assert edited_issue["source_span"] == "旧术语"
    assert edited_issue["target_span"] == "old term"
    assert detail["segments"][0]["versions"]["manual"] == "Use the preferred term."
    assert orchestrator.events[0][0][1] == "user.segment_edit"


def test_active_segment_edit_is_rejected_to_prevent_model_overwrite():
    run_id = _create_persisted_run(RunStatus.TRANSLATING)
    with SessionLocal() as session:
        segment_id = session.query(Segment).filter_by(run_id=run_id).one().id

    with pytest.raises(HTTPException) as caught:
        update_segment_translation(
            run_id,
            segment_id,
            SegmentEditRequest(translation="Manual edit"),
            orch=object(),
        )
    assert caught.value.status_code == 409
