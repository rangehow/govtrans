#!/usr/bin/env python3
"""Corpus ingestion CLI (E04). Examples:

  # local file pair (offline)
  python scripts/ingest_corpus.py --zh-file zh.html --en-file en.html --html \
      --zh-url http://www.scio.gov.cn/zfbps/xxx.htm --document-type white_paper

  # fetch from URLs (needs network)
  python scripts/ingest_corpus.py --zh-url ... --en-url ... --fetch
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from services.corpus.crawler import CrawlError, fetch_scio_document
from services.corpus.ingest import ingest_document_pair


def main() -> int:
    ap = argparse.ArgumentParser(description="Ingest a zh/en document pair into the corpus")
    ap.add_argument("--zh-file")
    ap.add_argument("--en-file")
    ap.add_argument("--zh-url")
    ap.add_argument("--en-url")
    ap.add_argument("--fetch", action="store_true", help="fetch both URLs via the crawler")
    ap.add_argument("--html", action="store_true", help="inputs are HTML")
    ap.add_argument("--document-type", default=None)
    ap.add_argument("--domain", default=None)
    ap.add_argument(
        "--promote-to-tm", action="store_true",
        help="explicitly publish high-scoring pairs into translation memory",
    )
    args = ap.parse_args()

    if args.fetch:
        if not (args.zh_url and args.en_url):
            ap.error("--fetch requires --zh-url and --en-url")
        try:
            zh_source = fetch_scio_document(args.zh_url).html
            en_source = fetch_scio_document(args.en_url).html
        except CrawlError as exc:
            print(f"FAIL: {exc}")
            return 2
    elif args.zh_file and args.en_file:
        zh_source = open(args.zh_file, encoding="utf-8").read()
        en_source = open(args.en_file, encoding="utf-8").read()
    else:
        ap.error("provide --zh-file/--en-file or --fetch with URLs")

    result = ingest_document_pair(
        zh_source=zh_source, en_source=en_source,
        is_html=args.html or args.fetch,
        zh_url=args.zh_url, en_url=args.en_url,
        document_type=args.document_type, domain=args.domain,
        match_method="url_heuristic" if args.fetch else "cli",
        promote=args.promote_to_tm,
    )
    print(f"OK pair={result.pair_id}")
    print(f"  paragraph pairs : {result.paragraph_pairs}")
    print(f"  sentence pairs  : {result.sentence_pairs}")
    print(f"  dedup dropped   : {result.dedup_dropped}")
    print(f"  promoted to TM  : {result.promoted_to_tm}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
