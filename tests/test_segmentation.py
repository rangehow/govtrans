import pytest

from services.orchestrator.segmentation import infer_block_kind, split_source_text


pytestmark = pytest.mark.unit


def test_short_paragraphs_keep_document_structure():
    text = "标题\n\n第一段。还在第一段。\n第二段。"
    assert split_source_text(text, max_chars=200) == ["标题", "第一段。还在第一段。", "第二段。"]


def test_long_paragraph_splits_without_loss():
    text = "推动高质量发展。" * 80
    segments = split_source_text(text, max_chars=120)
    assert len(segments) > 1
    assert all(0 < len(segment) <= 120 for segment in segments)
    assert "".join(segments) == text


def test_oversized_sentence_uses_soft_boundaries():
    text = "，".join(["协调推进各项工作"] * 50)
    segments = split_source_text(text, max_chars=100)
    assert all(len(segment) <= 100 for segment in segments)
    assert "".join(segments).replace("，", "") == text.replace("，", "")


def test_visual_line_wraps_are_restored_before_segmentation():
    text = "我们要坚持系统观念，\n统筹推进各项重点任务。\n第二段保持独立。"
    assert split_source_text(text, max_chars=200) == [
        "我们要坚持系统观念，统筹推进各项重点任务。",
        "第二段保持独立。",
    ]


def test_short_visual_line_wraps_are_not_mistaken_for_headings():
    text = "坚持人民至上\n推进共同富裕\n不断改善民生。"
    assert split_source_text(text, max_chars=200) == [
        "坚持人民至上推进共同富裕不断改善民生。",
    ]


def test_spacing_scripts_keep_word_boundaries_when_visual_lines_are_joined():
    text = "Реформа продолжается\nво всех регионах.\nСледующий абзац."
    assert split_source_text(text, max_chars=200) == [
        "Реформа продолжается во всех регионах.",
        "Следующий абзац.",
    ]


def test_document_roles_distinguish_title_heading_list_and_prose():
    assert infer_block_kind("政府工作报告", index=0) == "title"
    assert infer_block_kind("一、总体要求", index=1) == "heading"
    assert infer_block_kind("• 推进重点任务", index=2) == "list"
    assert infer_block_kind("各项工作取得新进展。", index=3) == "paragraph"
