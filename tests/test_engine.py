import asyncio

import pytest

from apps.api.config import get_settings
from apps.api.db import SessionLocal
from agents.roles import llm as roles_llm
from services.orchestrator import engine as engine_mod
from services.orchestrator.engine import (
    Orchestrator,
    _binding_glossary,
    _model_segment_id,
    _normalize_common_term_case,
    _normalize_finding,
    _resolve_model_segment_id,
)
from services.orchestrator.skills import load_skill_terms
from services.orchestrator.models import (
    DocumentGlossary,
    Issue,
    RunEvent,
    RunStatus,
    Segment,
    TranslationRun,
)
from services.orchestrator.tofu_client import TofuError

pytestmark = pytest.mark.unit


def make_fake_call_role(translate_map: dict[str, str], finalize_map: dict[str, str] | None = None):
    """Lightweight fake for the LLM boundary (per testing convention)."""

    async def fake(*, prompt_name, variables, **kwargs):
        if prompt_name == "analyze":
            return {
                "document_type": "policy_document",
                "domain": "economy",
                "summary": "A test document.",
                "key_points": ["growth"],
                "tone": "formal",
            }
        if prompt_name == "term_extract":
            return {
                "terms": [
                    {
                        "source": "高质量发展",
                        "proposed_target": "high-quality development",
                        "proper_name": False,
                        "needs_official_check": False,
                    }
                ]
            }
        if prompt_name == "translate_batch":
            return {
                "segments": [
                    {"id": item["id"], "translation": translate_map[item["source"]]}
                    for item in variables["segments"]
                    if item["needs_translation"]
                ],
                "uncertainties": [],
            }
        if prompt_name in {"document_review", "coherence_review"}:
            return {"issues": []}
        if prompt_name == "finalize_batch":
            return {
                "segments": [
                    {
                        "id": item["id"],
                        "final_translation": (finalize_map or {}).get(
                            item["source"], item["current_translation"]
                        ),
                        "changes": [
                            {
                                "before": item["current_translation"],
                                "after": (finalize_map or {}).get(
                                    item["source"], item["current_translation"]
                                ),
                                "reason_category": "issue_fix",
                            }
                        ],
                    }
                    for item in variables["segments"]
                ]
            }
        if prompt_name == "finalize":
            text = (finalize_map or {}).get(variables["source"], variables["translation"])
            return {
                "final_translation": text,
                "changes": [
                    {
                        "before": variables["translation"],
                        "after": text,
                        "reason_category": "issue_fix",
                    }
                ],
            }
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
    monkeypatch.setattr(roles_llm, "call_role", make_fake_call_role(translate_map, finalize_map))
    run_id = orch.create_run(source_text=source, confidentiality="PUBLIC")
    asyncio.run(orch.execute(run_id))
    with SessionLocal() as session:
        run = session.get(TranslationRun, run_id)
        segments = session.query(Segment).filter_by(run_id=run_id).order_by(Segment.idx).all()
        events = session.query(RunEvent).filter_by(run_id=run_id).all()
        issues = session.query(Issue).filter_by(run_id=run_id).all()
        glossary = session.query(DocumentGlossary).filter_by(run_id=run_id).first()
    return run, segments, events, issues, glossary


class TestModelSegmentBoundary:
    def test_short_alias_maps_back_to_database_id(self):
        segment = {"id": "a" * 32, "idx": 4, "source": "第五段", "translation": "P5"}
        assert _model_segment_id(segment) == "P5"
        assert _resolve_model_segment_id("P5", [segment]) == ("a" * 32, "alias")

    def test_spliced_uuid_is_repaired_by_unique_prefix(self):
        batch = [
            {
                "id": "8c7cf5c4514046528f0c1d8c6433abe5",
                "idx": 0,
                "source": "第一段",
                "translation": "Paragraph one",
            },
            {
                "id": "c759f0027dd54fa487fac42a64bb3e68",
                "idx": 4,
                "source": "第五段",
                "translation": "Paragraph five",
            },
        ]
        resolved, method = _resolve_model_segment_id(
            "c759f0027dd54fa487fac42a6433abe5", batch
        )
        assert resolved == "c759f0027dd54fa487fac42a64bb3e68"
        assert method == "uuid_prefix"

    def test_ambiguous_unknown_id_is_not_attached_to_a_paragraph(self):
        batch = [
            {"id": "a" * 32, "idx": 0, "source": "共同措辞", "translation": "same"},
            {"id": "b" * 32, "idx": 1, "source": "共同措辞", "translation": "same"},
        ]
        assert _resolve_model_segment_id(
            "invented", batch, {"source_span": "共同措辞"}
        ) == (None, "unresolved")


def test_multilingual_run_persists_pair_and_passes_it_to_every_model_role(
    orchestrator, monkeypatch
):
    source = "La coopération multilatérale progresse."
    base_fake = make_fake_call_role({source: "Die multilaterale Zusammenarbeit schreitet voran."})
    seen: list[tuple[str, str, str]] = []

    async def fake(*, prompt_name, variables, **kwargs):
        seen.append(
            (
                prompt_name,
                variables.get("source_language", ""),
                variables.get("target_language", ""),
            )
        )
        return await base_fake(prompt_name=prompt_name, variables=variables, **kwargs)

    monkeypatch.setattr(roles_llm, "call_role", fake)
    run_id = orchestrator.create_run(
        source_text=source,
        confidentiality="PUBLIC",
        document_type="white_paper",
        source_language="fr",
        target_language="de",
    )
    asyncio.run(orchestrator.execute(run_id))

    with SessionLocal() as session:
        run = session.get(TranslationRun, run_id)
        segment = session.query(Segment).filter_by(run_id=run_id).one()

    assert run.status == RunStatus.COMPLETED
    assert (run.direction, run.source_language, run.target_language) == ("fr-de", "fr", "de")
    assert run.style_skills == []
    assert set(run.version_pins["skill_versions"]) == {"gov-multilingual-core"}
    assert segment.translation == "Die multilaterale Zusammenarbeit schreitet voran."
    assert seen
    assert all(
        source_name == "French" and target_name == "German" for _, source_name, target_name in seen
    )


class TestHappyPath:
    def test_run_completes(self, orchestrator, monkeypatch):
        source = "推动高质量发展，加快构建新发展格局。"
        run, segments, events, issues, glossary = run_and_fetch(
            orchestrator,
            monkeypatch,
            source,
            {
                source: "We will promote high-quality development and accelerate the new development pattern."
            },
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

    def test_glossary_contract_separates_binding_terms_and_normalizes_common_case(self):
        entries = [
            {"source": "国务院", "target": "the State Council", "mandatory": True},
            {"source": "国内生产总值", "target": "Gross Domestic Product", "mandatory": False},
        ]
        assert _binding_glossary(entries) == entries[:1]
        assert _normalize_common_term_case("Gross Domestic Product (GDP)") == (
            "gross domestic product (GDP)"
        )
        assert load_skill_terms("gov-cn-en-core") == {}

    def test_objective_capitalization_note_cannot_be_downgraded_to_minor(self):
        finding = _normalize_finding(
            {
                "severity": "minor",
                "category": "style",
                "message": "Gross Domestic Product should not be capitalized here.",
            }
        )
        assert finding["severity"] == "major"

    def test_minor_review_note_does_not_trigger_rewrite(self, orchestrator, monkeypatch):
        source = "推进高质量发展。"
        draft = "We will promote high-quality development."

        async def fake(*, prompt_name, role, variables, **kwargs):
            if prompt_name == "analyze":
                return {
                    "document_type": "policy_document",
                    "domain": "economy",
                    "summary": "s",
                    "key_points": [],
                    "tone": "formal",
                }
            if prompt_name == "term_extract":
                return {"terms": []}
            if prompt_name == "translate_batch":
                return {
                    "segments": [
                        {"id": item["id"], "translation": draft}
                        for item in variables["segments"]
                        if item["needs_translation"]
                    ],
                    "uncertainties": [],
                }
            if prompt_name in {"document_review", "coherence_review"}:
                if role == "style_reviewer":
                    return {
                        "issues": [
                            {
                                "segment_id": variables["segments"][0]["id"],
                                "severity": "minor",
                                "category": "style",
                                "message": "subjective polish",
                                "suggested_fix": "rewrite",
                            }
                        ]
                    }
                return {"issues": []}
            if prompt_name == "finalize":
                raise AssertionError("minor-only notes must not invoke the finalizer")
            raise AssertionError(prompt_name)

        monkeypatch.setattr(roles_llm, "call_role", fake)
        run_id = orchestrator.create_run(source_text=source, confidentiality="PUBLIC")
        asyncio.run(orchestrator.execute(run_id))
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            segment = session.query(Segment).filter_by(run_id=run_id).one()
            minor = session.query(Issue).filter_by(run_id=run_id, severity="minor").one()
        assert run.status == RunStatus.COMPLETED
        assert segment.translation == draft
        assert minor.status == "open"

    def test_events_have_monotonic_seq(self, orchestrator, monkeypatch):
        source = "深化改革。"
        _, _, events, _, _ = run_and_fetch(
            orchestrator, monkeypatch, source, {source: "Deepen reform."}
        )
        seqs = sorted(e.seq for e in events)
        assert seqs == list(range(1, len(seqs) + 1))

    def test_overload_waits_persistently_then_resumes_without_human_action(
        self, orchestrator, monkeypatch
    ):
        run_id = orchestrator.create_run(source_text="深化改革。", confidentiality="PUBLIC")
        calls = 0

        async def flaky_execute(current_run_id):
            nonlocal calls
            calls += 1
            if calls == 1:
                raise TofuError("overloaded", "capacity", status=503, retryable=True)
            with SessionLocal() as session:
                run = session.get(TranslationRun, current_run_id)
                run.status = RunStatus.COMPLETED
                session.commit()

        async def no_wait(_seconds):
            return None

        monkeypatch.setattr(orchestrator, "execute", flaky_execute)
        monkeypatch.setattr(engine_mod.asyncio, "sleep", no_wait)
        asyncio.run(orchestrator._guarded_execute(run_id))

        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            waits = session.query(RunEvent).filter_by(run_id=run_id, type="run.resource_wait").all()
        assert calls == 2
        assert run.status == RunStatus.COMPLETED
        assert len(waits) == 1
        assert "1/3" in waits[0].summary

    def test_overload_wait_is_bounded_and_fails_with_saved_progress(
        self, orchestrator, monkeypatch
    ):
        run_id = orchestrator.create_run(source_text="深化改革。", confidentiality="PUBLIC")
        calls = 0

        async def always_overloaded(_run_id):
            nonlocal calls
            calls += 1
            raise TofuError("overloaded", "capacity", status=503, retryable=True)

        async def no_wait(_seconds):
            return None

        monkeypatch.setattr(orchestrator, "execute", always_overloaded)
        monkeypatch.setattr(engine_mod.asyncio, "sleep", no_wait)
        asyncio.run(orchestrator._guarded_execute(run_id))

        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            waits = (
                session.query(RunEvent).filter_by(run_id=run_id, type="run.resource_wait").count()
            )
        assert calls == orchestrator.settings.tofu_resource_max_waits + 1
        assert waits == orchestrator.settings.tofu_resource_max_waits
        assert run.status == RunStatus.FAILED
        assert "已停止无限等待" in run.error

    def test_long_stage_emits_honest_unchanged_progress_heartbeats(
        self, orchestrator, monkeypatch
    ):
        run_id = orchestrator.create_run(source_text="深化改革。", confidentiality="PUBLIC")
        monkeypatch.setattr(orchestrator.settings, "run_progress_heartbeat_seconds", 0.01)

        async def slow_executor(_run_id):
            await asyncio.sleep(0.025)

        asyncio.run(
            orchestrator._run_with_heartbeats(
                run_id,
                engine_mod.STAGES[1],
                slow_executor,
                {"kind": "model", "models": ["test-model"]},
                1 / len(engine_mod.STAGES),
            )
        )

        with SessionLocal() as session:
            heartbeats = (
                session.query(RunEvent)
                .filter_by(run_id=run_id, type="stage.analyze.heartbeat")
                .order_by(RunEvent.seq)
                .all()
            )
        assert heartbeats
        assert all(event.progress == 1 / len(engine_mod.STAGES) for event in heartbeats)
        assert all(event.metrics["heartbeat"] is True for event in heartbeats)


class TestReleaseGate:
    def test_critical_issue_loops_to_finalize_then_completes(self, orchestrator, monkeypatch):
        source = "2023年经济增长5.2%。"
        # First draft drops the number -> deterministic_qa critical -> finalize fixes it.
        run, segments, events, issues, _ = run_and_fetch(
            orchestrator,
            monkeypatch,
            source,
            {source: "In 2023, the economy grew."},
            {source: "In 2023, the economy grew by 5.2%."},
        )
        assert run.status == RunStatus.COMPLETED
        assert "5.2%" in segments[0].translation
        det = [i for i in issues if i.reviewer == "deterministic_qa"]
        assert det and det[0].severity == "critical"
        assert det[0].status == "resolved"

        visible_progress = [
            event.progress
            for event in sorted(events, key=lambda event: event.seq)
            if event.progress is not None
        ]
        assert visible_progress == sorted(visible_progress)

    def test_unfixable_critical_pauses_with_saved_work(self, orchestrator, monkeypatch):
        source = "2023年经济增长5.2%。"
        # Finalizer never fixes the number -> autonomous loop cap -> quality failure.
        run, _, _, _, _ = run_and_fetch(
            orchestrator,
            monkeypatch,
            source,
            {source: "In 2023, the economy grew."},
            {source: "In 2023, the economy grew."},
        )
        assert run.status == RunStatus.QUALITY_GATE_FAILED
        assert "本轮暂停" in run.error
        assert run.loop_count == orchestrator.settings.max_finalize_loops
        with SessionLocal() as session:
            open_critical = (
                session.query(Issue)
                .filter_by(run_id=run.id, status="open", severity="critical")
                .count()
            )
        assert open_critical > 0

    def test_quality_failure_can_continue_with_saved_work(self, orchestrator, monkeypatch):
        source = "2023年经济增长5.2%。"
        run, _, _, _, _ = run_and_fetch(
            orchestrator,
            monkeypatch,
            source,
            {source: "In 2023, the economy grew."},
            {source: "In 2023, the economy grew."},
        )
        assert run.status == RunStatus.QUALITY_GATE_FAILED

        monkeypatch.setattr(
            roles_llm,
            "call_role",
            make_fake_call_role({}, {source: "In 2023, the economy grew by 5.2%."}),
        )

        async def continue_and_wait():
            assert orchestrator.continue_quality(run.id)
            await orchestrator._tasks[run.id]

        asyncio.run(continue_and_wait())
        with SessionLocal() as session:
            resumed = session.get(TranslationRun, run.id)
            segment = session.query(Segment).filter_by(run_id=run.id).one()
        assert resumed.status == RunStatus.COMPLETED
        assert segment.translation.endswith("5.2%.")
        assert resumed.version_pins["previous_revision_rounds"] >= 1

    def test_technical_failure_can_retry_from_persisted_stage(self, orchestrator, monkeypatch):
        source = "统筹发展和安全。"
        run, _, _, _, _ = run_and_fetch(
            orchestrator,
            monkeypatch,
            source,
            {source: "Coordinate development and security."},
        )
        with SessionLocal() as session:
            failed = session.get(TranslationRun, run.id)
            failed.status = RunStatus.FAILED
            failed.current_stage = "final_qa"
            failed.error = "ValueError: simulated model output failure"
            session.commit()

        async def retry_and_wait():
            assert orchestrator.continue_quality(run.id)
            await orchestrator._tasks[run.id]

        asyncio.run(retry_and_wait())
        with SessionLocal() as session:
            retried = session.get(TranslationRun, run.id)
            retry_event = (
                session.query(RunEvent)
                .filter_by(run_id=run.id, type="run.failure_retry")
                .one()
            )
        assert retried.status == RunStatus.COMPLETED
        assert retried.error is None
        assert retry_event.metrics["resume_stage"] == "final_qa"


class TestTranslateConcurrency:
    def test_short_document_is_one_coherent_batch(self, orchestrator, monkeypatch):
        import services.orchestrator.engine as eng

        concurrent = 0
        peak = 0

        async def fake(*, prompt_name, variables, **kwargs):
            nonlocal concurrent, peak
            if prompt_name == "analyze":
                return {
                    "document_type": "policy_document",
                    "domain": "economy",
                    "summary": "s",
                    "key_points": [],
                    "tone": "formal",
                }
            if prompt_name == "term_extract":
                return {"terms": []}
            if prompt_name == "translate_batch":
                concurrent += 1
                peak = max(peak, concurrent)
                await asyncio.sleep(0.05)
                concurrent -= 1
                return {
                    "segments": [
                        {"id": item["id"], "translation": f"EN: {item['source']}"}
                        for item in variables["segments"]
                        if item["needs_translation"]
                    ],
                    "uncertainties": [],
                }
            if prompt_name in {"document_review", "coherence_review"}:
                return {"issues": []}
            raise AssertionError(prompt_name)

        async def no_search(*a, **k):
            return []

        monkeypatch.setattr(roles_llm, "call_role", fake)
        monkeypatch.setattr(eng, "official_search", no_search)
        source = "\n".join(f"第{i}段内容。" for i in range(10))
        run_id = orchestrator.create_run(source_text=source, confidentiality="PUBLIC")
        asyncio.run(orchestrator.execute(run_id))
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            done = session.query(Segment).filter_by(run_id=run_id, status="final").count()
        assert run.status == RunStatus.COMPLETED
        assert done == 10
        assert peak == 1
        with SessionLocal() as session:
            batch_events = (
                session.query(RunEvent)
                .filter(
                    RunEvent.run_id == run_id,
                    RunEvent.type.in_(["translate.batch", "translate.early_batch"]),
                )
                .all()
            )
        assert len(batch_events) == 1
        assert batch_events[0].metrics["paragraphs"] == 10

    def test_independent_preflight_and_review_stages_run_in_parallel(
        self, orchestrator, monkeypatch
    ):
        active = {"preflight": 0, "startup": 0, "review": 0}
        peaks = {"preflight": 0, "startup": 0, "review": 0}

        async def fake(*, prompt_name, variables, role, **kwargs):
            if prompt_name in {"analyze", "term_extract"}:
                active["preflight"] += 1
                active["startup"] += 1
                peaks["preflight"] = max(peaks["preflight"], active["preflight"])
                peaks["startup"] = max(peaks["startup"], active["startup"])
                await asyncio.sleep(0.03)
                active["preflight"] -= 1
                active["startup"] -= 1
                if prompt_name == "analyze":
                    return {
                        "document_type": "policy_document",
                        "domain": "economy",
                        "summary": "s",
                        "key_points": [],
                        "tone": "formal",
                    }
                return {"terms": []}
            if prompt_name == "translate_batch":
                active["startup"] += 1
                peaks["startup"] = max(peaks["startup"], active["startup"])
                await asyncio.sleep(0.03)
                active["startup"] -= 1
                return {
                    "segments": [
                        {"id": item["id"], "translation": f"EN: {item['source']}"}
                        for item in variables["segments"]
                        if item["needs_translation"]
                    ],
                    "uncertainties": [],
                }
            if prompt_name in {"document_review", "coherence_review"}:
                active["review"] += 1
                peaks["review"] = max(peaks["review"], active["review"])
                await asyncio.sleep(0.03)
                active["review"] -= 1
                return {"issues": []}
            raise AssertionError((role, prompt_name))

        monkeypatch.setattr(roles_llm, "call_role", fake)
        run_id = orchestrator.create_run(
            source_text="第一段内容。\n第二段内容。", confidentiality="PUBLIC"
        )
        asyncio.run(orchestrator.execute(run_id))
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
        assert run.status == RunStatus.COMPLETED
        assert peaks == {"preflight": 2, "startup": 3, "review": 3}

    def test_short_document_repairs_are_finalized_in_one_joint_call(
        self, orchestrator, monkeypatch
    ):
        finalize_batch_sizes: list[int] = []

        async def fake(*, prompt_name, variables, role, **kwargs):
            if prompt_name == "analyze":
                return {
                    "document_type": "policy_document",
                    "domain": "economy",
                    "summary": "s",
                    "key_points": [],
                    "tone": "formal",
                }
            if prompt_name == "term_extract":
                return {"terms": []}
            if prompt_name == "translate_batch":
                return {
                    "segments": [
                        {"id": item["id"], "translation": f"EN: {item['source']}"}
                        for item in variables["segments"]
                        if item["needs_translation"]
                    ],
                    "uncertainties": [],
                }
            if prompt_name in {"document_review", "coherence_review"}:
                if role != "semantic_reviewer":
                    return {"issues": []}
                return {
                    "issues": [
                        {
                            "segment_id": item["id"],
                            "severity": "major",
                            "category": "semantic",
                            "message": "repair",
                            "suggested_fix": "repair",
                        }
                        for item in variables["segments"]
                    ]
                }
            if prompt_name == "finalize_batch":
                finalize_batch_sizes.append(len(variables["segments"]))
                return {
                    "segments": [
                        {
                            "id": item["id"],
                            "final_translation": item["current_translation"],
                            "changes": [],
                        }
                        for item in variables["segments"]
                    ]
                }
            raise AssertionError((role, prompt_name))

        monkeypatch.setattr(roles_llm, "call_role", fake)
        run_id = orchestrator.create_run(
            source_text="甲段。\n乙段。\n丙段。", confidentiality="PUBLIC"
        )
        asyncio.run(orchestrator.execute(run_id))
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
        assert run.status == RunStatus.COMPLETED
        assert finalize_batch_sizes == [3]

    def test_unsafe_finalizer_rewrite_cannot_corrupt_a_hard_fact(
        self, orchestrator, monkeypatch
    ):
        source = "部署需要数百万日元投资。"

        async def fake(*, prompt_name, variables, role, **kwargs):
            if prompt_name == "analyze":
                return {
                    "document_type": "report",
                    "domain": "technology",
                    "summary": "s",
                    "key_points": [],
                    "tone": "formal",
                }
            if prompt_name == "term_extract":
                return {"terms": []}
            if prompt_name == "translate_batch":
                return {
                    "segments": [
                        {
                            "id": item["id"],
                            "translation": "Deployment requires an investment of several million Japanese yen.",
                        }
                        for item in variables["segments"]
                        if item["needs_translation"]
                    ],
                    "uncertainties": [],
                }
            if prompt_name in {"document_review", "coherence_review"}:
                if role != "semantic_reviewer":
                    return {"issues": []}
                return {
                    "issues": [
                        {
                            "segment_id": variables["segments"][0]["id"],
                            "severity": "major",
                            "category": "semantic",
                            "message": "Rewrite the sentence.",
                            "suggested_fix": "Use a more direct sentence.",
                        }
                    ]
                }
            if prompt_name == "finalize_batch":
                return {
                    "segments": [
                        {
                            "id": variables["segments"][0]["id"],
                            "final_translation": "Deployment requires several million RMB.",
                            "changes": [
                                {
                                    "before": "Japanese yen",
                                    "after": "RMB",
                                    "reason_category": "issue_fix",
                                }
                            ],
                        }
                    ]
                }
            raise AssertionError((role, prompt_name))

        monkeypatch.setattr(roles_llm, "call_role", fake)
        run_id = orchestrator.create_run(source_text=source, confidentiality="PUBLIC")
        asyncio.run(orchestrator.execute(run_id))
        with SessionLocal() as session:
            run = session.get(TranslationRun, run_id)
            segment = session.query(Segment).filter_by(run_id=run_id).one()
            issue = session.query(Issue).filter_by(run_id=run_id).one()
        assert run.status == RunStatus.COMPLETED
        assert "Japanese yen" in segment.translation
        assert issue.status == "dismissed"


class TestIdempotency:
    def test_reexecute_completed_run_is_noop(self, orchestrator, monkeypatch):
        source = "统筹发展和安全。"
        run, segments, events, _, _ = run_and_fetch(
            orchestrator, monkeypatch, source, {source: "Ensure both development and security."}
        )
        n_events = len(events)
        asyncio.run(orchestrator.execute(run.id))
        with SessionLocal() as session:
            again = session.query(RunEvent).filter_by(run_id=run.id).count()
        assert again == n_events
