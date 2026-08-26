import pytest

from services.quality import validators as v

pytestmark = pytest.mark.unit


class TestNumberValidator:
    def test_all_numbers_present(self):
        findings = v.validate_numbers(
            "增长6.5%，总量101.2万亿", "grew 6.5%, reaching 101.2 trillion"
        )
        assert findings == []

    def test_missing_number_is_critical(self):
        findings = v.validate_numbers("增长6.5%，就业1200万", "grew 6.5%")
        assert len(findings) == 1
        assert findings[0]["severity"] == "critical"
        assert findings[0]["category"] == "number"
        assert "1200" in findings[0]["source_span"]

    def test_comma_normalization(self):
        assert v.validate_numbers("总量12000亿", "total 12,000") == []

    def test_month_name_consumes_semantically_equivalent_month_digit(self):
        source = "据新华社北京8月24日电"
        translation = "According to Xinhua News Agency, Beijing, August 24"
        assert v.validate_numbers(source, translation) == []

    def test_date_consumption_does_not_hide_a_second_missing_number(self):
        source = "8月24日发布8项措施"
        translation = "Measures were released on August 24."
        # The date's 8 is consumed independently; the unrelated second 8 must
        # still be reported.
        findings = v.validate_numbers(source, translation)
        assert [finding["source_span"] for finding in findings] == ["8"]

    def test_month_only_name_is_semantically_equal_to_source_digit(self):
        assert v.validate_numbers("7月中旬发布", "published in mid-July") == []

    def test_month_only_consumption_does_not_hide_another_missing_number(self):
        findings = v.validate_numbers(
            "7月中旬发布7项措施", "Measures were published in mid-July."
        )
        assert [finding["source_span"] for finding in findings] == ["7"]

    def test_digits_inside_model_names_are_not_counted_as_quantities(self):
        source = "K3优于GPT-5.6；文中再次提到K3。"
        translation = "K3 outperformed GPT-5.6."
        assert v.validate_numbers(source, translation) == []

    def test_english_ordinal_suffix_is_still_a_quantity(self):
        assert v.validate_numbers("排名第33位", "ranked 33rd") == []


class TestDateValidator:
    def test_iso_match(self):
        assert v.validate_dates("2024年3月5日", "on 2024-03-05") == []

    def test_english_month_match(self):
        assert v.validate_dates("2024年3月5日", "on March 5, 2024") == []

    def test_missing_date_flagged(self):
        findings = v.validate_dates("2024年3月5日发布", "released recently")
        assert len(findings) == 1
        assert findings[0]["severity"] == "critical"

    @pytest.mark.parametrize(
        "translation",
        [
            "BEIJING, Aug. 24",
            "Beijing, August 24",
            "published on 24 August",
            "published on 8/24",
        ],
    )
    def test_month_day_without_year(self, translation):
        assert v.validate_dates("北京8月24日电", translation) == []

    def test_month_day_must_be_a_date_not_unrelated_digits(self):
        findings = v.validate_dates("北京8月24日电", "There were 8 groups and 24 delegates.")
        assert len(findings) == 1
        assert findings[0]["category"] == "date"


class TestCurrencyValidator:
    def test_explicit_currency_is_preserved(self):
        assert v.validate_currencies(
            "需要数百万日元投资", "requires an investment of several million Japanese yen"
        ) == []

    def test_currency_substitution_is_critical(self):
        findings = v.validate_currencies(
            "需要数百万日元投资", "requires an investment of several million RMB"
        )
        assert findings and findings[0]["severity"] == "critical"

    def test_model_suggestion_cannot_remove_correct_currency(self):
        finding = {
            "category": "semantic",
            "message": "Japanese yen should be replaced with RMB currency.",
            "suggested_fix": "several million RMB",
        }
        assert v.finding_conflicts_with_currency_anchor(
            "需要数百万日元投资",
            "requires an investment of several million Japanese yen",
            finding,
        )

    def test_unrelated_style_suggestion_is_not_filtered(self):
        finding = {
            "category": "style",
            "message": "Use a more concise opening.",
            "suggested_fix": "The project requires investment.",
        }
        assert not v.finding_conflicts_with_currency_anchor(
            "需要数百万日元投资",
            "requires an investment of several million Japanese yen",
            finding,
        )


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

    def test_unverified_suggestion_is_not_release_blocking(self):
        glossary = [
            {
                "source": "全球治理",
                "target": "Global Governance",
                "origin": "llm_proposed",
                "mandatory": False,
            }
        ]
        assert v.validate_terminology("完善全球治理", "improve global governance", glossary) == []

    def test_common_term_title_case_is_blocking_when_rendering_is_used(self):
        glossary = [
            {
                "source": "国内生产总值",
                "target": "gross domestic product (GDP)",
                "origin": "llm_proposed",
                "mandatory": False,
                "proper_name": False,
            }
        ]
        findings = v.validate_term_capitalization(
            "国内生产总值增长5%",
            "The Gross Domestic Product (GDP) grew by 5%.",
            glossary,
        )
        assert findings and findings[0]["severity"] == "major"

    def test_common_term_sentence_case_and_proper_names_are_accepted(self):
        glossary = [
            {
                "source": "国内生产总值",
                "target": "gross domestic product (GDP)",
                "proper_name": False,
            },
            {"source": "国务院", "target": "State Council", "proper_name": True},
        ]
        assert (
            v.validate_term_capitalization(
                "国务院发布国内生产总值",
                "The State Council released gross domestic product (GDP) data.",
                glossary,
            )
            == []
        )

    def test_binding_term_preserves_curated_internal_case(self):
        glossary = [
            {
                "source": "国内生产总值",
                "target": "gross domestic product (GDP)",
                "mandatory": True,
                "origin": "translation_skill",
            }
        ]
        findings = v.validate_term_capitalization(
            "国内生产总值增长5%",
            "The Gross Domestic Product (GDP) grew by 5%.",
            glossary,
        )
        assert findings and findings[0]["category"] == "capitalization"

    def test_common_term_is_capitalized_after_dateline_and_opening_quote(self):
        glossary = [
            {
                "source": "树立和践行正确政绩观",
                "target": "establishing and practicing the correct concept of achievements",
                "origin": "llm_proposed",
                "mandatory": False,
                "proper_name": False,
            }
        ]
        translation = (
            'According to Xinhua News Agency, Beijing, August 24 — "Establishing '
            'and practicing the correct concept of achievements is a long-term task."'
        )
        assert (
            v.validate_term_capitalization(
                "树立和践行正确政绩观是一项长期任务", translation, glossary
            )
            == []
        )

    def test_only_initial_capital_is_sentence_case_not_title_case(self):
        glossary = [
            {
                "source": "国内生产总值",
                "target": "gross domestic product (GDP)",
                "proper_name": False,
            }
        ]
        assert (
            v.validate_term_capitalization(
                "国内生产总值增长", "Gross domestic product (GDP) grew.", glossary
            )
            == []
        )


def test_non_english_pair_skips_english_only_rules_but_keeps_universal_checks():
    glossary = [
        {
            "source": "高质量发展",
            "target": "Développement de Haute Qualité",
            "mandatory": False,
            "proper_name": False,
        }
    ]
    findings = v.run_deterministic(
        "2024年3月5日推动高质量发展，增长5%。",
        ("Le 5 mars 2024, promouvoir le Développement de Haute Qualité, avec 5 % de croissance."),
        glossary,
        source_language="zh",
        target_language="fr",
    )
    assert not any(item["category"] in {"date", "capitalization"} for item in findings)
    assert not any(item["category"] == "number" for item in findings)

    missing = v.run_deterministic(
        "增长5%。",
        "La croissance a augmenté.",
        source_language="zh",
        target_language="fr",
    )
    assert any(item["category"] == "number" for item in missing)
