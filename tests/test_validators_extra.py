import pytest

from services.quality import validators as v

pytestmark = pytest.mark.unit


class TestAcronymValidator:
    def test_acronym_preserved(self):
        assert v.validate_acronyms("GDP增长，CPC领导", "GDP grew under CPC leadership") == []

    def test_acronym_dropped(self):
        findings = v.validate_acronyms("GDP增长6%", "the economy grew 6%")
        assert any(f["source_span"] == "GDP" for f in findings)
        assert findings[0]["severity"] == "major"


class TestEntityValidator:
    def test_title_rendered(self):
        src = "《新时代的中国绿色发展》白皮书"
        tgt = 'the white paper "Green Development in the New Era"'
        assert v.validate_entities(src, tgt) == []

    def test_title_dropped(self):
        src = "《决定》全文发布"
        tgt = "the full text was released"
        findings = v.validate_entities(src, tgt)
        assert len(findings) == 1
        assert findings[0]["category"] == "entity"

    def test_no_titles_no_findings(self):
        assert v.validate_entities("深化改革", "deepen reform") == []


class TestEnumerationValidator:
    def test_parallel_list_ok(self):
        src = "稳增长、调结构、促改革"
        tgt = "stabilize growth, adjust the structure, and advance reform"
        assert v.validate_enumerations(src, tgt) == []

    def test_list_collapsed(self):
        src = "稳增长、调结构、促改革、惠民生"
        tgt = "stabilize growth"
        findings = v.validate_enumerations(src, tgt)
        assert len(findings) == 1
        assert findings[0]["category"] == "enumeration"

    def test_no_enumeration(self):
        assert v.validate_enumerations("深化改革。", "Deepen reform.") == []


class TestRunDeterministicAggregation:
    def test_runs_all_validators(self):
        findings = v.run_deterministic(
            "2023年GDP增长5.2%，坚持稳增长、调结构。",
            "In 2023 the economy grew.",
        )
        categories = {f["category"] for f in findings}
        assert "number" in categories
        assert "acronym" in categories
