"""Supported languages and pair-level capability policy.

Model translation is language-pair agnostic. Evidence, terminology and style
assets are explicitly scoped so a missing corpus can never masquerade as an
unsupported translation direction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class LanguageSpec:
    code: str
    name_zh: str
    name_en: str
    bcp47: str
    rtl: bool = False


_LANGUAGE_SPECS = (
    LanguageSpec("zh", "简体中文", "Simplified Chinese", "zh-CN"),
    LanguageSpec("en", "英语", "English", "en-US"),
    LanguageSpec("ja", "日语", "Japanese", "ja-JP"),
    LanguageSpec("ko", "韩语", "Korean", "ko-KR"),
    LanguageSpec("fr", "法语", "French", "fr-FR"),
    LanguageSpec("de", "德语", "German", "de-DE"),
    LanguageSpec("es", "西班牙语", "Spanish", "es-ES"),
    LanguageSpec("pt", "葡萄牙语", "Portuguese", "pt-PT"),
    LanguageSpec("it", "意大利语", "Italian", "it-IT"),
    LanguageSpec("ru", "俄语", "Russian", "ru-RU"),
    LanguageSpec("uk", "乌克兰语", "Ukrainian", "uk-UA"),
    LanguageSpec("ar", "阿拉伯语", "Arabic", "ar", rtl=True),
    LanguageSpec("hi", "印地语", "Hindi", "hi-IN"),
    LanguageSpec("th", "泰语", "Thai", "th-TH"),
    LanguageSpec("vi", "越南语", "Vietnamese", "vi-VN"),
    LanguageSpec("id", "印度尼西亚语", "Indonesian", "id-ID"),
    LanguageSpec("tr", "土耳其语", "Turkish", "tr-TR"),
    LanguageSpec("nl", "荷兰语", "Dutch", "nl-NL"),
    LanguageSpec("pl", "波兰语", "Polish", "pl-PL"),
)

LANGUAGES = {item.code: item for item in _LANGUAGE_SPECS}
_ALIASES = {
    "zh-cn": "zh",
    "zh-hans": "zh",
    "cn": "zh",
    "en-us": "en",
    "en-gb": "en",
    "jp": "ja",
    "kr": "ko",
    "pt-br": "pt",
    "pt-pt": "pt",
}


def normalize_language(code: str | None) -> str:
    normalized = (code or "").strip().casefold().replace("_", "-")
    normalized = _ALIASES.get(normalized, normalized)
    if normalized not in LANGUAGES:
        supported = ", ".join(LANGUAGES)
        raise ValueError(f"不支持的语言代码 {code!r}；可用：{supported}")
    return normalized


def resolve_language_pair(
    source_language: str | None = None,
    target_language: str | None = None,
    direction: str | None = None,
) -> tuple[str, str]:
    """Resolve the new pair fields while accepting the legacy direction API."""
    if direction and (source_language is None and target_language is None):
        parts = direction.strip().casefold().replace("_", "-").split("-")
        if len(parts) != 2:
            raise ValueError("direction 必须是 source-target，例如 zh-en")
        source_language, target_language = parts
    source = normalize_language(source_language or "zh")
    target = normalize_language(target_language or "en")
    if source == target:
        raise ValueError("源语言和目标语言不能相同")
    if direction:
        expected = f"{source}-{target}"
        normalized_direction = direction.strip().casefold().replace("_", "-")
        if normalized_direction != expected:
            raise ValueError(f"direction={direction!r} 与 source_language/target_language 不一致")
    return source, target


def language_spec(code: str) -> LanguageSpec:
    return LANGUAGES[normalize_language(code)]


def pair_capabilities(source_language: str, target_language: str) -> dict:
    source, target = resolve_language_pair(source_language, target_language)
    direct_corpus = source == "zh" and target == "en"
    reverse_corpus = source == "en" and target == "zh"
    official_corpus = direct_corpus or reverse_corpus
    specialized_style = direct_corpus
    if direct_corpus:
        description = "模型翻译 + 中英官方语料 + 政务英语专项文风与增强校验"
    elif reverse_corpus:
        description = "模型翻译 + 中英官方语料反向参考 + 通用多语种审校"
    else:
        description = "模型原生多语种翻译 + 语言对术语 + 通用确定性与模型审校"
    return {
        "model_translation": True,
        "pair_scoped_terminology": True,
        "official_corpus": official_corpus,
        "official_corpus_direction": (
            "direct" if direct_corpus else "reverse" if reverse_corpus else "none"
        ),
        "specialized_style": specialized_style,
        "qa_tier": "zh_en_enhanced" if direct_corpus else "multilingual_universal",
        "description": description,
    }


def language_pair_payload(source_language: str, target_language: str) -> dict:
    source, target = resolve_language_pair(source_language, target_language)
    return {
        "source": asdict(LANGUAGES[source]),
        "target": asdict(LANGUAGES[target]),
        "direction": f"{source}-{target}",
        "capabilities": pair_capabilities(source, target),
    }


def language_catalog_payload() -> dict:
    return {
        "languages": [asdict(item) for item in _LANGUAGE_SPECS],
        "defaults": {"source_language": "zh", "target_language": "en"},
        "enhanced_pairs": [
            language_pair_payload("zh", "en"),
            language_pair_payload("en", "zh"),
        ],
        "policy": (
            "所有已列语言可由模型双向互译；官方语料、规定术语和专项文风按语言对单独声明，"
            "缺少语料只会降低证据增强等级，不会禁用翻译。"
        ),
    }


def prompt_language_variables(source_language: str, target_language: str) -> dict[str, str]:
    source, target = resolve_language_pair(source_language, target_language)
    source_spec = LANGUAGES[source]
    target_spec = LANGUAGES[target]
    return {
        "source_language": source_spec.name_en,
        "target_language": target_spec.name_en,
        "source_language_code": source,
        "target_language_code": target,
    }
