"""Style distillation: phrase/pattern mining over aligned pairs (§19).

Mining strategy (deterministic, no LLM):
1. Collect approved/high-scoring sentence pairs.
2. For each known zh construction cue (frame patterns like 以…为…, 坚持…,
   排比、…与…), find the English rendering actually used in official text.
3. A candidate rule is kept when the same cue maps to the same rendering in
   >= MIN_SUPPORT distinct pairs (confidence = consistent/total).
4. Everything lands in style_rules with status='candidate' — humans approve
   before a skill version is cut (review step in §19).
"""
from __future__ import annotations

import re
from collections import defaultdict

from sqlalchemy import select

from apps.api.db import SessionLocal
from pipelines.style_distillation.models import StyleRule
from services.corpus.models import AlignedPair

MIN_SUPPORT = 2
MIN_PAIR_SCORE = 0.5

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


def mine_candidate_rules(*, min_support: int = MIN_SUPPORT) -> dict:
    """Scan aligned pairs and upsert candidate StyleRules. Returns stats."""
    with SessionLocal() as session:
        pairs = session.execute(
            select(AlignedPair).where(
                AlignedPair.level == "sentence",
                AlignedPair.score >= MIN_PAIR_SCORE,
                AlignedPair.status != "rejected",
            )
        ).scalars().all()
        pair_rows = [(p.id, p.zh_text, p.en_text) for p in pairs]
        # doc domain lookup for rule domains
        from services.corpus.models import CorpusDocument, DocumentPair
        domains: dict[str, str] = {}
        for p in pairs:
            doc_pair = session.get(DocumentPair, p.pair_id)
            if doc_pair:
                zh_doc = session.get(CorpusDocument, doc_pair.zh_doc_id)
                if zh_doc and zh_doc.domain:
                    domains[p.id] = zh_doc.domain

    # cue -> family -> [(pair_id, zh, en)]
    hits: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    totals: dict[str, int] = defaultdict(int)
    for pair_id, zh, en in pair_rows:
        for cue in CUE_PATTERNS:
            if not cue["zh_re"].search(zh):
                continue
            totals[cue["key"]] += 1
            for family, en_re in cue["en_families"].items():
                if en_re.search(en):
                    hits[cue["key"]][family].append((pair_id, zh, en))
                    break  # first matching family wins for this pair

    created = updated = 0
    with SessionLocal() as session:
        for cue_key, families in hits.items():
            total = totals[cue_key]
            for family, examples in families.items():
                support = len(examples)
                if support < min_support:
                    continue
                confidence = round(support / total, 3)
                zh_pattern = cue_key
                rule_text = f"{cue_key} 句式优先译为 {family} 系列表达"
                example_dicts = [{"zh": zh, "en": en, "pair_id": pid}
                                 for pid, zh, en in examples[:5]]
                counters = [
                    {"zh": zh, "en": en, "pair_id": pid}
                    for pid, zh, en in [
                        (p.id, p.zh_text, p.en_text) for p in pairs
                        if cue_key in p.zh_text and (p.id, p.zh_text, p.en_text)
                        not in {(e[0], e[1], e[2]) for e in examples}
                    ][:3]
                ]
                existing = session.execute(
                    select(StyleRule).where(
                        StyleRule.zh_pattern == zh_pattern,
                        StyleRule.en_rendering == family,
                    )
                ).scalar_one_or_none()
                rule_domains = sorted({domains.get(pid) for pid, _, _ in examples} - {None})
                if existing:
                    existing.source_count = support
                    existing.confidence = confidence
                    existing.examples = example_dicts
                    existing.counterexamples = counters
                    existing.domains = rule_domains
                    updated += 1
                else:
                    session.add(StyleRule(
                        rule=rule_text, zh_pattern=zh_pattern, en_rendering=family,
                        examples=example_dicts, counterexamples=counters,
                        source_count=support, domains=rule_domains,
                        confidence=confidence, status="candidate",
                    ))
                    created += 1
        session.commit()
    return {"pairs_scanned": len(pair_rows), "created": created, "updated": updated,
            "cues_hit": len(hits)}
