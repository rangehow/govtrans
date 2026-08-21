import asyncio

import pytest

from apps.api.config import get_settings
from apps.api.db import SessionLocal
from agents.roles import llm as roles_llm
from services.orchestrator import engine as engine_mod
from services.orchestrator.engine import Orchestrator
from services.orchestrator.models import (
    DocumentGlossary,
    Issue,
    RunEvent,
    RunStatus,
    Segment,
    TranslationRun,
)

pytestmark = pytest.mark.unit


def make_fake_call_role(translate_map: dict[str, str], finalize_map: dict[str, str] | None = None):
    """Lightweight fake for the LLM boundary (per testing convention)."""
    async def fake(*, prompt_name, variables, **kwargs):
        if prompt_name == "analyze":
            return {"document_type": "policy_document", "domain": "economy",
                    "summary": "A test document.", "key_points": ["growth"], "tone": "formal"}
        if prompt_name == "term_extract":
            return {"terms": [{"source": "高质量发展", "proposed_target": "high-quality development",
                               "needs_official_check": False}]}
        if prompt_name == "translate_segment":
            return {"translation": translate_map[variables["source_segment"]],
                    "terms_used": [], "evidence_refs": [], "uncertainties": []}
        if prompt_name == "review":
            return {"issues": []}
        if prompt_name == "finalize":
            text = (finalize_map or {}).get(
                variables["source"], variables["translation"])
            return {"final_translation": text,
                    "changes": [{"before": variables["translation"], "after": text,
                                 "reason_category": "issue_fix"}]}
        raise AssertionError(f"unexpected prompt {prompt_name}")
    return fake


@pytest.fixture
def orchestrator(monkeypatch):
    async def no_search(*args, **kwargs):
        return []
    monkeypatch.setattr(engine_mod, "official_search", no_search)
    settings = get_settings()
    return Orchestrator(settings, tofu=None)  # tofu unused: call_role is faked


def run_and_fetch(orch, monkeypatch, source, translate_map, finalize_map=None):
    monkeypatch.setattr(roles_llm, "call_role",
                        make_fake_call_role(translate_map, finalize_map))
    run_id = orch.create_run(source_text=source, confidentiality="PUBLIC")
    asyncio.run(orch.execute(run_id))
    with SessionLocal() as session:
        run = session.get(TranslationRun, run_id)
        segments = session.query(Segment).filter_by(run_id=run_id).order_by(Segment.idx).all()
        events = session.query(RunEvent).filter_by(run_id=run_id).all()
        issues = session.query(Issue).filter_by(run_id=run_id).all()
        glossary = session.query(DocumentGlossary).filter_by(run_id=run_id).first()
    return run, segments, events, issues, glossary


class TestHappyPath:
    def test_run_completes(self, orchestrator, monkeypatch):
        source = "推动高质量发展，加快构建新发展格局。"
        run, segments, events, issues, glossary = run_and_fetch(
            orchestrator, monkeypatch, source,
            {source: "We will promote high-quality development and accelerate the new development pattern."},
        )
        assert run.status == RunStatus.COMPLETED
        assert run.progress == 1.0
        assert segments[0].status == "final"
        assert segments[0].versions["ai_draft"]
        assert segments[0].versions["final"]
        assert glossary is not None
        assert glossary.entries[0]["source"] == "高质量发展"
        phases = {e.phase for e in events}
        assert {"parse", "analyze", "terminology", "translate", "final_qa"} <= phases

    def test_events_have_monotonic_seq(self, orchestrator, monkeypatch):
        source = "深化改革。"
        _, _, events, _, _ = run_and_fetch(
            orchestrator, monkeypatch, source, {source: "Deepen reform."})
        seqs = sorted(e.seq for e in events)
        assert seqs == list(range(1, len(seqs) + 1))


class TestReleaseGate:
    def test_critical_issue_loops_to_finalize_then_completes(self, orchestrator, monkeypatch):
        source = "2023年经济增长5.2%。"
        # First draft drops the number -> deterministic_qa critical -> finalize fixes it.
        run, segments, _, issues, _ = run_and_fetch(
            orchestrator, monkeypatch, source,
            {source: "In 2023, the economy grew."},
            {source: "In 2023, the economy grew by 5.2%."},
        )
        assert run.status == RunStatus.COMPLETED
        assert "5.2%" in segments[0].translation
        det = [i for i in issues if i.reviewer == "deterministic_qa"]
        assert det and det[0].severity == "critical"
        assert det[0].status == "resolved"

    def test_unfixable_critical_waits_for_human(self, orchestrator, monkeypatch):
        source = "2023年经济增长5.2%。"
        # Finalizer never fixes the number -> loop cap -> WAITING_HUMAN_REVIEW.
        run, _, _, _, _ = run_and_fetch(
            orchestrator, monkeypatch, source,
            {source: "In 2023, the economy grew."},
            {source: "In 2023, the economy grew."},
        )
        assert run.status == RunStatus.WAITING_HUMAN_REVIEW
        assert run.loop_count == orchestrator.settings.max_finalize_loops
        with SessionLocal() as session:
            open_critical = session.query(Issue).filter_by(
                run_id=run.id, status="open", severity="critical").count()
        assert open_critical > 0


class TestIdempotency:
    def test_reexecute_completed_run_is_noop(self, orchestrator, monkeypatch):
        source = "统筹发展和安全。"
        run, segments, events, _, _ = run_and_fetch(
            orchestrator, monkeypatch, source,
            {source: "Ensure both development and security."})
        n_events = len(events)
        asyncio.run(orchestrator.execute(run.id))
        with SessionLocal() as session:
            again = session.query(RunEvent).filter_by(run_id=run.id).count()
        assert again == n_events
