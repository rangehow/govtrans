import pytest

from services.languages import (
    language_catalog_payload,
    language_pair_payload,
    normalize_language,
    resolve_language_pair,
)
from services.orchestrator.skills import (
    base_skills_for,
    resolve_style_skills,
)
from services.retrieval.bm25 import tokenize


pytestmark = pytest.mark.unit


def test_catalog_exposes_multilingual_translation_without_corpus_gate():
    catalog = language_catalog_payload()
    assert len(catalog["languages"]) >= 19
    pair = language_pair_payload("fr", "de")
    assert pair["direction"] == "fr-de"
    assert pair["capabilities"]["model_translation"] is True
    assert pair["capabilities"]["official_corpus"] is False


def test_pair_resolution_keeps_legacy_direction_and_rejects_same_language():
    assert normalize_language("zh_CN") == "zh"
    assert resolve_language_pair(direction="en-zh") == ("en", "zh")
    with pytest.raises(ValueError, match="不能相同"):
        resolve_language_pair("ja", "ja")


def test_style_assets_are_pair_scoped_but_generic_translation_is_always_on():
    assert base_skills_for("fr", "de") == ["gov-multilingual-core"]
    assert "gov-cn-en-core" in base_skills_for("zh", "en")
    assert (
        resolve_style_skills(None, "white_paper", source_language="fr", target_language="de") == []
    )
    with pytest.raises(ValueError, match="仅有 zh-en"):
        resolve_style_skills(
            ["gov-white-paper"],
            None,
            source_language="fr",
            target_language="de",
        )


def test_bm25_tokenizer_keeps_cyrillic_arabic_and_cjk_searchable():
    tokens = tokenize("改革 реформа الإصلاح")
    assert "改革" in tokens
    assert "реформа" in tokens
    assert "الإصلاح" in tokens
