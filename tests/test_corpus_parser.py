import pytest

from services.corpus.parser import extract_metadata, parse_html, split_sentences

pytestmark = pytest.mark.unit

HTML = """
<html><head><title>中国的能源转型（全文）</title></head>
<body>
<nav>导航噪音应该被忽略</nav>
<h1>中国的能源转型</h1>
<p>2024年8月29日 来源：新华社</p>
<p>中国坚持绿色发展，能源转型取得历史性成就。2023年清洁能源消费比重达到26.4%。</p>
<p>十年来，中国累计淘汰煤电落后产能超过1亿千瓦。</p>
<ul><li>坚持节约优先。</li></ul>
<script>var x = 1;</script>
</body></html>
"""


class TestParseHtml:
    def test_blocks_and_title(self):
        blocks, title = parse_html(HTML)
        assert title == "中国的能源转型（全文）"
        texts = [b["text"] for b in blocks]
        assert any("绿色发展" in t for t in texts)
        assert not any("导航噪音" in t for t in texts)
        assert not any("var x" in t for t in texts)

    def test_block_kinds(self):
        blocks, _ = parse_html(HTML)
        kinds = {b["kind"] for b in blocks}
        assert "heading" in kinds and "paragraph" in kinds and "list_item" in kinds

    def test_metadata(self):
        meta = extract_metadata(HTML, "http://www.scio.gov.cn/zfbps/x.htm", "t")
        assert meta["publish_date"] == "2024-08-29"
        assert meta["source"] == "新华社"
        assert meta["host"] == "www.scio.gov.cn"


class TestSentenceSplit:
    def test_zh(self):
        sents = split_sentences("坚持绿色发展。能源转型取得成就！未来继续努力？", "zh")
        assert len(sents) == 3

    def test_en_keeps_decimals(self):
        sents = split_sentences("It grew 26.4%. The share kept rising. Next year matters.", "en")
        assert len(sents) == 3
        assert "26.4%" in sents[0]
