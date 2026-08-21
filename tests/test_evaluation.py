import asyncio

import pytest

from agents.roles import llm as roles_llm
from apps.api.config import get_settings
from apps.api.db import SessionLocal
from evaluation.gold_set import load_gold_set
from evaluation.metrics import chrf, numbers_score, term_score
from evaluation.models import BenchmarkRun
from evaluation.runner import compare_to_baseline, run_baseline, run_benchmark
from services.orchestrator import engine as engine_mod
from services.orchestrator.engine import Orchestrator

pytestmark = pytest.mark.unit


class TestMetrics:
    def test_chrf_identical(self):
        assert chrf("Promote high-quality development.",
                    "Promote high-quality development.") == 1.0

    def test_chrf_partial(self):
        score = chrf("Promote good development.", "Promote high-quality development.")
        assert 0.3 < score < 1.0

    def test_chrf_empty(self):
        assert chrf("", "reference") == 0.0

    def test_numbers_score(self):
        assert numbers_score("增长5.2%", "grew 5.2%") == 1.0
        assert numbers_score("增长5.2%", "grew") == 0.0

    def test_term_score(self):
        glossary = [{"source": "高质量发展", "target": "high-quality development"}]
        assert term_score("推动高质量发展", "promote high-quality development", glossary) == 1.0
        assert term_score("推动高质量发展", "promote good development", glossary) == 0.0


class TestGoldSet:
    def test_load_seed(self):
        gold = load_gold_set("seed_gov_zh_en")
        assert len(gold.items) >= 3
        assert gold.version == "0.1.0"
        assert gold.items[0].glossary

    def test_missing_set_actionable(self):
        with pytest.raises(FileNotFoundError, match="available"):
            load_gold_set("nonexistent")


@pytest.fixture
def faked_orchestrator(monkeypatch):
    async def no_search(*a, **k):
        return []

    async def fake_call_role(*, prompt_name, variables, **kwargs):
        if prompt_name == "analyze":
            return {"document_type": "policy_document", "domain": "economy",
                    "summary": "s", "key_points": [], "tone": "formal"}
        if prompt_name == "term_extract":
            return {"terms": []}
        if prompt_name == "translate_segment":
            # decent-but-imperfect pipeline translation
            return {"translation": "Promote high-quality development and accelerate "
                                   "the creation of a new development pattern.",
                    "terms_used": [], "evidence_refs": [], "uncertainties": []}
        if prompt_name == "review":
            return {"issues": []}
        if prompt_name == "finalize":
            return {"final_translation": variables["translation"], "changes": []}
        if prompt_name == "baseline_translate":
            # baseline is weaker (drops the second clause)
            return {"translation": "Promote high-quality development."}
        raise AssertionError(prompt_name)

    monkeypatch.setattr(engine_mod, "official_search", no_search)
    monkeypatch.setattr(roles_llm, "call_role", fake_call_role)
    return Orchestrator(get_settings(), tofu=None)


class TestBenchmarkFlow:
    def test_pipeline_beats_baseline_verdict(self, faked_orchestrator):
        bench_id = asyncio.run(run_benchmark(faked_orchestrator, gold_set_name="seed_gov_zh_en"))
        base_id = asyncio.run(
            run_baseline(faked_orchestrator.settings, None, gold_set_name="seed_gov_zh_en"))
        with SessionLocal() as session:
            bench = session.get(BenchmarkRun, bench_id)
            base = session.get(BenchmarkRun, base_id)
            assert bench.status == "completed" and base.status == "completed"
            assert bench.metrics["items"] == 3
            assert bench.kind == "pipeline" and base.kind == "baseline"
            # pipeline covers both clauses -> higher chrF than truncated baseline
            assert bench.metrics["chrf"] > base.metrics["chrf"]
        comparison = compare_to_baseline(bench_id)
        assert comparison["verdict"] == "pass"
        assert comparison["deltas"]["chrf"] > 0
