"""Style distillation: phrase/pattern mining over aligned pairs (§19).

Mining strategy (deterministic, no LLM):
1. Collect approved/high-scoring sentence pairs.
2. For each known zh construction cue (frame patterns like 以…为…, 坚持…,
   排比、…与…), find the English rendering actually used in official text.
3. A candidate rule is kept only when the same cue maps to the same rendering
   across >= MIN_SUPPORT distinct official documents (confidence is still
   measured over all sentence observations).
4. High-confidence rules from independently sourced SCIO document pairs
   become active automatically; weaker observations remain optional evidence.
"""
from __future__ import annotations

import re
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from apps.api.db import SessionLocal
from pipelines.style_distillation.models import StyleRule
from services.corpus.models import AlignedPair

MIN_SUPPORT = 2
MIN_PAIR_SCORE = 0.72
AUTO_PUBLISH_CONFIDENCE = 0.8

# zh construction cues -> regex over en to recognize the rendering family.
# This is the seed pattern set; mining also proposes free n-gram cues later.
CUE_PATTERNS: list[dict] = [
    {"key": "以…为…", "zh_re": re.compile(r"以[^，。；]{1,12}?为[^，。；]{1,12}?[，。；]"),
     "en_families": {"take-as": re.compile(r"\btake\b[^.]*\bas\b", re.I),
                     "regard-as": re.compile(r"\bregard\b[^.]*\bas\b", re.I),
                     "with-as": re.compile(r"\bwith\b[^.]*\bas\b", re.I)}},
    {"key": "坚持…", "zh_re": re.compile(r"坚持[^，。；]{1,20}"),
     "en_families": {"uphold": re.compile(r"\buphold\b", re.I),
                     "remain-committed": re.compile(r"\bremain committed\b", re.I),
                     "adhere": re.compile(r"\badher\w+\b", re.I),
                     "pursue": re.compile(r"\bpursu\w+\b", re.I)}},
    {"key": "全面…", "zh_re": re.compile(r"全面[^，。；]{1,20}"),
     "en_families": {"fully": re.compile(r"\bfully\b", re.I),
                     "comprehensive": re.compile(r"\bcomprehensiv\w+\b", re.I),
                     "all-round": re.compile(r"\ball[- ]round\b", re.I)}},
    {"key": "加快…", "zh_re": re.compile(r"加快[^，。；]{1,20}"),
     "en_families": {"accelerate": re.compile(r"\baccelerat\w+\b", re.I),
                     "speed-up": re.compile(r"\bspeed up\b|\bfast[- ]track\w*\b", re.I)}},
    {"key": "推动/推进…", "zh_re": re.compile(r"(?:推动|推进)[^，。；]{1,20}"),
     "en_families": {"promote": re.compile(r"\bpromot\w+\b", re.I),
                     "advance": re.compile(r"\badvanc\w+\b", re.I),
                     "push-forward": re.compile(r"\bpush\w* forward\b", re.I)}},
    {"key": "…共同体", "zh_re": re.compile(r"[^，。；]{1,15}共同体"),
     "en_families": {"community-shared-future": re.compile(r"communit\w+[^.]*shared future", re.I),
                     "community": re.compile(r"\bcommunit\w+\b", re.I)}},
]


def mine_candidate_rules(
    *, min_support: int = MIN_SUPPORT, official_only: bool = True
) -> dict:
    """Mine style evidence and conservatively auto-publish strong SCIO rules."""
    with SessionLocal() as session:
        pairs = session.execute(
            select(AlignedPair).where(
                AlignedPair.level == "sentence",
                AlignedPair.score >= MIN_PAIR_SCORE,
                AlignedPair.status != "rejected",
            )
        ).scalars().all()
        pair_rows: list[dict[str, str]] = []
        # doc domain lookup for rule domains
        from services.corpus.models import CorpusDocument, DocumentPair
        domains: dict[str, str] = {}
        for p in pairs:
            doc_pair = session.get(DocumentPair, p.pair_id)
            if doc_pair:
                zh_doc = session.get(CorpusDocument, doc_pair.zh_doc_id)
                en_doc = session.get(CorpusDocument, doc_pair.en_doc_id)
                is_scio = bool(
                    zh_doc and en_doc
                    and (zh_doc.url or "").startswith(
                        ("http://www.scio.gov.cn/", "https://www.scio.gov.cn/")
                    )
                    and (en_doc.url or "").startswith(
                        ("http://english.scio.gov.cn/", "https://english.scio.gov.cn/")
                    )
                )
                if official_only and not is_scio:
                    continue
                pair_rows.append({
                    "id": p.id,
                    "document_pair_id": p.pair_id,
                    "zh": p.zh_text,
                    "en": p.en_text,
                })
                if zh_doc and zh_doc.domain:
                    domains[p.id] = zh_doc.domain

    # cue -> family -> evidence rows
    hits: dict[str, dict[str, list[dict[str, str]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    observations: dict[str, list[dict[str, str]]] = defaultdict(list)
    totals: dict[str, int] = defaultdict(int)
    for evidence in pair_rows:
        zh, en = evidence["zh"], evidence["en"]
        for cue in CUE_PATTERNS:
            if not cue["zh_re"].search(zh):
                continue
            totals[cue["key"]] += 1
            observations[cue["key"]].append(evidence)
            for family, en_re in cue["en_families"].items():
                if en_re.search(en):
                    hits[cue["key"]][family].append(evidence)
                    break  # first matching family wins for this pair

    created = updated = auto_activated = 0
    active_rule_keys: set[tuple[str, str]] = set()
    with SessionLocal() as session:
        for cue_key, families in hits.items():
            total = totals[cue_key]
            for family, examples in families.items():
                support = len(examples)
                document_support = len({item["document_pair_id"] for item in examples})
                if document_support < min_support:
                    continue
                active_rule_keys.add((cue_key, family))
                confidence = round(support / total, 3)
                zh_pattern = cue_key
                rule_text = f"{cue_key} 句式优先译为 {family} 系列表达"
                example_dicts = [
                    {
                        "zh": item["zh"],
                        "en": item["en"],
                        "pair_id": item["id"],
                        "document_pair_id": item["document_pair_id"],
                    }
                    for item in examples[:5]
                ]
                example_ids = {item["id"] for item in examples}
                counters = [
                    {
                        "zh": item["zh"],
                        "en": item["en"],
                        "pair_id": item["id"],
                        "document_pair_id": item["document_pair_id"],
                    }
                    for item in observations[cue_key]
                    if item["id"] not in example_ids
                ]
                counters = counters[:3]
                existing = session.execute(
                    select(StyleRule).where(
                        StyleRule.zh_pattern == zh_pattern,
                        StyleRule.en_rendering == family,
                    )
                ).scalar_one_or_none()
                rule_domains = sorted(
                    {domains.get(item["id"]) for item in examples} - {None}
                )
                if existing:
                    existing.source_count = document_support
                    existing.confidence = confidence
                    existing.examples = example_dicts
                    existing.counterexamples = counters
                    existing.domains = rule_domains
                    if (
                        existing.status == "candidate"
                        and confidence >= AUTO_PUBLISH_CONFIDENCE
                    ):
                        existing.status = "approved"
                        existing.activation_source = "automatic"
                        existing.activated_at = datetime.now(timezone.utc)
                        auto_activated += 1
                    elif (
                        existing.status == "approved"
                        and existing.activation_source == "automatic"
                        and confidence < AUTO_PUBLISH_CONFIDENCE
                    ):
                        # Automatically mined rules also deactivate
                        # automatically when the evidence no longer meets the
                        # contract. Human exceptions are never silently reset.
                        existing.status = "candidate"
                        existing.activation_source = None
                        existing.activated_at = None
                    updated += 1
                else:
                    status = (
                        "approved"
                        if confidence >= AUTO_PUBLISH_CONFIDENCE
                        else "candidate"
                    )
                    auto_active = status == "approved"
                    session.add(StyleRule(
                        rule=rule_text, zh_pattern=zh_pattern, en_rendering=family,
                        examples=example_dicts, counterexamples=counters,
                        source_count=document_support, domains=rule_domains,
                        confidence=confidence, status=status,
                        activation_source="automatic" if auto_active else None,
                        activated_at=datetime.now(timezone.utc) if auto_active else None,
                    ))
                    created += 1
                    auto_activated += int(auto_active)
        stale_candidates = session.execute(
            select(StyleRule).where(StyleRule.status == "candidate")
        ).scalars().all()
        for candidate in stale_candidates:
            if (candidate.zh_pattern, candidate.en_rendering) not in active_rule_keys:
                session.delete(candidate)
        session.commit()
    return {
        "pairs_scanned": len(pair_rows), "created": created, "updated": updated,
        "documents_scanned": len({row["document_pair_id"] for row in pair_rows}),
        "auto_activated": auto_activated,
        # Kept for old sync-job payloads and API clients during migration.
        "auto_published": auto_activated,
        "cues_hit": len(hits),
    }
