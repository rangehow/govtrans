import json
import zipfile
from io import BytesIO

import pytest

from services.export.exporters import FORMATS, export_run

pytestmark = pytest.mark.unit

RUN = {
    "id": "abcdef1234567890", "direction": "zh-en", "status": "COMPLETED",
    "summary": "Test export", "confidentiality": "PUBLIC",
    "pipeline_version": "0.1.0", "version_pins": {},
}
SEGMENTS = [
    {"idx": 0, "source": "推动高质量发展。", "translation": "Promote high-quality development.",
     "versions": {"ai_draft": "Promote high-quality development."}},
    {"idx": 1, "source": "2023年增长5.2%。", "translation": "In 2023 it grew by 5.2%.",
     "versions": {"ai_draft": "In 2023 it grew by 5.2%.", "final": "In 2023 it grew by 5.2%."}},
]


class TestTextFormats:
    def test_txt(self):
        out = export_run(RUN, SEGMENTS, "txt")
        text = out.content.decode()
        assert "Promote high-quality development." in text
        assert out.filename.endswith(".txt")

    def test_md_has_metadata(self):
        out = export_run(RUN, SEGMENTS, "md")
        text = out.content.decode()
        assert "zh-en" in text and "0.1.0" in text

    def test_json_roundtrip(self):
        out = export_run(RUN, SEGMENTS, "json")
        payload = json.loads(out.content)
        assert payload["segments"][1]["versions"]["final"] == "In 2023 it grew by 5.2%."


class TestInterchangeFormats:
    def test_xliff(self):
        out = export_run(RUN, SEGMENTS, "xliff")
        text = out.content.decode()
        assert "<xliff version=\"1.2\"" in text
        assert "<source xml:lang=\"zh-CN\">推动高质量发展。</source>" in text
        assert text.count("<trans-unit") == 2

    def test_tmx(self):
        out = export_run(RUN, SEGMENTS, "tmx")
        text = out.content.decode()
        assert '<tuv xml:lang="zh-CN">' in text
        assert "5.2%" in text

    def test_escaping(self):
        segs = [{"idx": 0, "source": "含<标签>与\"引号\"", "translation": 'with <tag> & "quote"',
                 "versions": {}}]
        text = export_run(RUN, segs, "xliff").content.decode()
        assert "&lt;标签&gt;" in text and "&quot;" in text


class TestDocx:
    def test_docx_structure(self):
        out = export_run(RUN, SEGMENTS, "docx")
        assert out.content[:2] == b"PK"  # zip magic
        with zipfile.ZipFile(BytesIO(out.content)) as zf:
            names = zf.namelist()
            assert "word/document.xml" in names
            document = zf.read("word/document.xml").decode("utf-8")
        assert "Promote high-quality development." in document

    def test_bilingual_table(self):
        out = export_run(RUN, SEGMENTS, "docx_bilingual")
        with zipfile.ZipFile(BytesIO(out.content)) as zf:
            document = zf.read("word/document.xml").decode("utf-8")
        assert "推动高质量发展。" in document and "Promote high-quality development." in document
        assert "<w:tbl>" in document  # basic table preserved

    def test_all_formats_registered(self):
        assert set(FORMATS) == {"txt", "md", "json", "xliff", "tmx", "docx", "docx_bilingual"}
