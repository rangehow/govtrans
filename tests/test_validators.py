import pytest

from services.quality import validators as v

pytestmark = pytest.mark.unit


class TestNumberValidator:
    def test_all_numbers_present(self):
        findings = v.validate_numbers("增长6.5%，总量101.2万亿", "grew 6.5%, reaching 101.2 trillion")
        assert findings == []

    def test_missing_number_is_critical(self):
        findings = v.validate_numbers("增长6.5%，就业1200万", "grew 6.5%")
        assert len(findings) == 1
        assert findings[0]["severity"] == "critical"
        assert findings[0]["category"] == "number"
        assert "1200" in findings[0]["source_span"]

    def test_comma_normalization(self):
        assert v.validate_numbers("总量12000亿", "total 12,000") == []


class TestDateValidator:
    def test_iso_match(self):
        assert v.validate_dates("2024年3月5日", "on 2024-03-05") == []

    def test_english_month_match(self):
        assert v.validate_dates("2024年3月5日", "on March 5, 2024") == []

    def test_missing_date_flagged(self):
        findings = v.validate_dates("2024年3月5日发布", "released recently")
        assert len(findings) == 1
        assert findings[0]["severity"] == "critical"


class TestBracketQuoteValidators:
    def test_unbalanced_brackets(self):
        findings = v.validate_brackets("（重要）", "(important")
        assert len(findings) >= 1
        assert findings[0]["severity"] == "major"

    def test_balanced_ok(self):
        assert v.validate_brackets("（重要）", "(important)") == []

    def test_unbalanced_quotes(self):
        assert v.validate_quotes("说“你好”", 'said "hello') != []


class TestTerminologyValidator:
    GLOSSARY = [{"source": "高质量发展", "target": "high-quality development"}]

    def test_conformant(self):
        findings = v.validate_terminology(
            "推动高质量发展", "promote high-quality development", self.GLOSSARY
        )
        assert findings == []

    def test_deviation_is_critical(self):
        findings = v.validate_terminology(
            "推动高质量发展", "promote good development", self.GLOSSARY
        )
        assert len(findings) == 1
        assert findings[0]["severity"] == "critical"
        assert findings[0]["category"] == "terminology"

    def test_term_absent_from_source_ignored(self):
        assert v.validate_terminology("深化改革", "deepen reform", self.GLOSSARY) == []
