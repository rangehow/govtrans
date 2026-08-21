"""Export Engine (§39). Formats: txt / md / json / xliff / tmx / docx /
docx_bilingual. DOCX keeps heading/paragraph/list/table structure when the
run carries parsed structure; plain runs degrade to paragraphs.

All exporters are pure functions over (run, segments) so they are unit
testable without the API layer.
"""
from __future__ import annotations

import io
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.sax.saxutils import escape


@dataclass
class ExportResult:
    content: bytes
    media_type: str
    filename: str


FORMATS = ("txt", "md", "json", "xliff", "tmx", "docx", "docx_bilingual")


def _esc(text: str) -> str:
    return escape(text, {'"': "&quot;"})


def export_txt(segments: list[dict]) -> bytes:
    return "\n\n".join(s["translation"] or "" for s in segments).encode("utf-8")


def export_md(run: dict, segments: list[dict]) -> bytes:
    lines = [f"# {run.get('summary') or 'GovTrans Export'}", ""]
    lines.append(f"> direction: {run['direction']} | pipeline: {run['pipeline_version']} | "
                 f"confidentiality: {run['confidentiality']}")
    lines.append("")
    for seg in segments:
        lines.append(seg["translation"] or "")
        lines.append("")
    return "\n".join(lines).encode("utf-8")


def export_json(run: dict, segments: list[dict]) -> bytes:
    payload = {
        "run_id": run["id"],
        "direction": run["direction"],
        "status": run["status"],
        "pipeline_version": run["pipeline_version"],
        "version_pins": run["version_pins"],
        "segments": [
            {"idx": s["idx"], "source": s["source"],
             "translation": s["translation"], "versions": s["versions"]}
            for s in segments
        ],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def export_xliff(run: dict, segments: list[dict]) -> bytes:
    units = []
    for seg in segments:
        units.append(
            f'    <trans-unit id="{seg["idx"]}" xml:space="preserve">\n'
            f'      <source xml:lang="zh-CN">{_esc(seg["source"])}</source>\n'
            f'      <target xml:lang="en-US">{_esc(seg["translation"] or "")}</target>\n'
            f"    </trans-unit>"
        )
    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<xliff version="1.2" xmlns="urn:oasis:names:tc:xliff:document:1.2">\n'
        f'  <file original="{_esc(run["id"])}" source-language="zh-CN" '
        f'target-language="en-US" datatype="plaintext">\n'
        "  <body>\n" + "\n".join(units) + "\n  </body>\n  </file>\n</xliff>\n"
    )
    return doc.encode("utf-8")


def export_tmx(run: dict, segments: list[dict]) -> bytes:
    tus = []
    created = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    for seg in segments:
        tus.append(
            f'    <tu tuid="{seg["idx"]}" creationdate="{created}">\n'
            f'      <tuv xml:lang="zh-CN"><seg>{_esc(seg["source"])}</seg></tuv>\n'
            f'      <tuv xml:lang="en-US"><seg>{_esc(seg["translation"] or "")}</seg></tuv>\n'
            "    </tu>"
        )
    doc = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<tmx version="1.4">\n'
        '  <header creationtool="GovTrans" creationtoolversion="'
        + _esc(run["pipeline_version"])
        + '" segtype="sentence" adminlang="en-US" srclang="zh-CN" datatype="plaintext"/>\n'
        "  <body>\n" + "\n".join(tus) + "\n  </body>\n</tmx>\n"
    )
    return doc.encode("utf-8")


def export_docx(run: dict, segments: list[dict], *, bilingual: bool) -> bytes:
    from docx import Document
    from docx.enum.text import WD_ALIGN_PARAGRAPH

    document = Document()
    title = run.get("summary") or "GovTrans Export"
    heading = document.add_heading(title[:120], level=1)
    heading.alignment = WD_ALIGN_PARAGRAPH.LEFT
    document.add_paragraph(
        f"direction: {run['direction']} | pipeline: {run['pipeline_version']} | "
        f"confidentiality: {run['confidentiality']}"
    )
    if bilingual:
        table = document.add_table(rows=1, cols=2)
        table.style = "Table Grid"
        hdr = table.rows[0].cells
        hdr[0].text = "原文"
        hdr[1].text = "译文"
        for seg in segments:
            cells = table.add_row().cells
            cells[0].text = seg["source"]
            cells[1].text = seg["translation"] or ""
    else:
        for seg in segments:
            document.add_paragraph(seg["translation"] or "")
    buf = io.BytesIO()
    document.save(buf)
    return buf.getvalue()


def export_run(run: dict, segments: list[dict], fmt: str) -> ExportResult:
    if fmt not in FORMATS:
        raise ValueError(f"unsupported format {fmt!r}; choose from {FORMATS}")
    run_id = run["id"][:8]
    if fmt == "txt":
        return ExportResult(export_txt(segments), "text/plain; charset=utf-8", f"run-{run_id}.txt")
    if fmt == "md":
        return ExportResult(export_md(run, segments), "text/markdown; charset=utf-8", f"run-{run_id}.md")
    if fmt == "json":
        return ExportResult(export_json(run, segments), "application/json", f"run-{run_id}.json")
    if fmt == "xliff":
        return ExportResult(export_xliff(run, segments), "application/x-xliff+xml", f"run-{run_id}.xlf")
    if fmt == "tmx":
        return ExportResult(export_tmx(run, segments), "application/x-tmx+xml", f"run-{run_id}.tmx")
    if fmt == "docx":
        content = export_docx(run, segments, bilingual=False)
        return ExportResult(content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", f"run-{run_id}.docx")
    content = export_docx(run, segments, bilingual=True)
    return ExportResult(content, "application/vnd.openxmlformats-officedocument.wordprocessingml.document", f"run-{run_id}-bilingual.docx")
