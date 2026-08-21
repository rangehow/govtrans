"""Gold set loader (§36). Gold sets are versioned JSONL files under
evaluation/gold/: {"id", "source", "reference", "domain", "document_type",
"glossary"?: [{"source","target"}]}
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

GOLD_DIR = Path(__file__).resolve().parent / "gold"


@dataclass
class GoldItem:
    id: str
    source: str
    reference: str
    domain: str | None = None
    document_type: str | None = None
    glossary: list[dict] = field(default_factory=list)


@dataclass
class GoldSet:
    name: str
    version: str
    items: list[GoldItem]


def load_gold_set(name: str) -> GoldSet:
    path = GOLD_DIR / f"{name}.jsonl"
    if not path.is_file():
        available = sorted(p.stem for p in GOLD_DIR.glob("*.jsonl"))
        raise FileNotFoundError(f"gold set {name!r} not found; available: {available}")
    version = None
    items: list[GoldItem] = []
    for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = line.strip()
        if not line:
            continue
        record = json.loads(line)
        if record.get("id") == "__meta__":
            version = record.get("version")
            continue
        for key in ("id", "source", "reference"):
            if key not in record:
                raise ValueError(f"{path}:{lineno}: missing required field {key!r}")
        items.append(GoldItem(
            id=record["id"], source=record["source"], reference=record["reference"],
            domain=record.get("domain"), document_type=record.get("document_type"),
            glossary=record.get("glossary", []),
        ))
    if not items:
        raise ValueError(f"gold set {name!r} is empty")
    return GoldSet(name=name, version=version or "unversioned", items=items)
