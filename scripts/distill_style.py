#!/usr/bin/env python3
"""Style distillation CLI (E09): mine candidate style rules from the corpus.

    python scripts/distill_style.py [--min-support 2]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipelines.style_distillation.mine import mine_candidate_rules


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-support", type=int, default=2)
    args = ap.parse_args()
    stats = mine_candidate_rules(min_support=args.min_support)
    print(f"scanned {stats['pairs_scanned']} pairs; cues hit: {stats['cues_hit']}; "
          f"rules created: {stats['created']}, updated: {stats['updated']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
