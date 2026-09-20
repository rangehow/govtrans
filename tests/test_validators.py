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

    def test_yi_converts_to_billion_by_value(self):
        # 11亿 = 1.1 billion: digits legitimately differ after conversion.
        assert (
            v.validate_numbers("供电人口超过11亿", "serving over 1.1 billion people")
            == []
        )

    def test_yi_converts_to_million_rendering(self):
        assert v.validate_numbers("人口13亿", "a population of 1,300 million") == []

    def test_wan_converts_to_plain_expanded_integer(self):
        assert v.validate_numbers("新增就业1.2万人", "12,000 new jobs") == []

    def test_duo_after_number_still_matches_magnitude(self):
        findings = v.validate_numbers(
            "10亿多选民将直接选举产生260多万名代表",
            "more than 1 billion voters will directly elect over 2.6 million deputies",
        )
        assert findings == []

    def test_wan_yi_converts_to_trillion(self):
        assert v.validate_numbers("总量1万亿", "totalling 1 trillion") == []

    def test_same_digits_rescaling_is_a_critical_conversion_error(self):
        findings = v.validate_numbers("供电人口超过11亿", "serving over 11 billion people")
        assert len(findings) == 1
        assert findings[0]["severity"] == "critical"
        assert findings[0]["category"] == "number"
        assert "换算" in findings[0]["message"]

    def test_ten_yi_is_not_ten_billion(self):
        findings = v.validate_numbers("10亿多选民", "over 10 billion voters")
        assert findings and findings[0]["severity"] == "critical"

    def test_spelled_out_english_number_is_value_equivalent(self):
        # 10亿 -> "one billion": no arabic digits on the target side at all.
        assert (
            v.validate_numbers("10亿多选民", "more than one billion voters") == []
        )

    def test_spelled_out_hundred_rendering(self):
        assert v.validate_numbers("人口2亿", "a population of two hundred million") == []

    def test_spelled_out_rescaling_is_also_a_conversion_error(self):
        findings = v.validate_numbers("供电人口超过11亿", "over eleven billion people")
        assert findings and findings[0]["severity"] == "critical"
        assert "换算" in findings[0]["message"]

    def test_missing_number_fix_says_verify_by_value_not_copy_digits(self):
        findings = v.validate_numbers("增长6.5%，就业1200万", "grew 6.5%")
        assert "核对" in findings[0]["suggested_fix"]
        assert "补译数字" not in findings[0]["suggested_fix"]

    def test_feedback_example_one_full_sentence(self):
        source = (
            "经营区域覆盖我国26个省（自治区、直辖市），"
            "供电范围占国土面积的88%，供电人口超过11亿。"
        )
        translation = (
            "The State Grid Corporation (SGCC) operates across 26 provinces "
            "(autonomous regions and municipalities), with its power supply "
            "area covering 88% of the country's land area and serving a "
            "population exceeding 1.1 billion."
        )
        assert v.validate_numbers(source, translation) == []


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

    def test_advisory_term_never_forces_casing(self):
        # Auto-extracted suggestions can misclassify a proper compound as a
        # common term or store a wrongly cased target; enforcing them
        # lowercased names like "China-Arab". Only binding (human-confirmed)
        # terms carry a casing contract.
        glossary = [
            {
                "source": "国内生产总值",
                "target": "gross domestic product (GDP)",
                "origin": "llm_proposed",
                "mandatory": False,
                "proper_name": False,
            }
        ]
        assert (
            v.validate_term_capitalization(
                "国内生产总值增长5%",
                "The Gross Domestic Product (GDP) grew by 5%.",
                glossary,
            )
            == []
        )

    def test_advisory_lowercase_target_cannot_lowercase_proper_compound(self):
        glossary = [
            {
                "source": "中阿",
                "target": "china-arab",
                "origin": "llm_proposed",
                "mandatory": False,
                "proper_name": False,
            }
        ]
        assert (
            v.validate_term_capitalization(
                "中阿合作论坛", "The China-Arab States Cooperation Forum.", glossary
            )
            == []
        )

    def test_binding_proper_compound_keeps_internal_capitals(self):
        glossary = [
            {
                "source": "中阿",
                "target": "China-Arab",
                "origin": "term_db",
                "proper_name": True,
            }
        ]
        findings = v.validate_term_capitalization(
            "中阿合作", "the china-arab cooperation", glossary
        )
        assert findings and findings[0]["category"] == "capitalization"

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
