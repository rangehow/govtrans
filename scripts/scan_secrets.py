#!/usr/bin/env python3
"""GovTrans secret scanner — self-contained, zero third-party deps.

Scans every git-tracked file (``git ls-files``) for credential patterns and
exits non-zero on any finding. Run locally and in CI before every commit:

    python scripts/scan_secrets.py

A finding is a REAL-looking secret. Placeholders (``change-me``, empty
values, env-var references like ``$VAR`` / ``${VAR}`` / ``os.environ``) are
allowed — see PLACEHOLDER_MARKERS. To allow a vetted false positive, put the
exact matched string (not the file) into ALLOWLIST below with a comment.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# --- Patterns that are always a secret when matched ------------------------
PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("dashscope/openai-style key", re.compile(r"\bsk-[A-Za-z0-9]{16,}\b")),
    ("private key block", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    ("aws access key id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("github token", re.compile(r"\b(?:ghp|gho|ghu|ghs|ghr|github_pat)_[A-Za-z0-9_]{20,}\b")),
    ("generic assignment", re.compile(
        r"(?i)(?:api[_-]?key|secret[_-]?key|access[_-]?key|password|passwd|token)"
        r"\s*[:=]\s*[\"']([^\"'\s]{8,})[\"']"
    )),
]

PLACEHOLDER_MARKERS = (
    "change-me", "changeme", "example", "placeholder", "your-", "dummy",
    "test", "fake", "redacted", "***", "xxx", "<", ">", "$", "os.environ",
    "env.", "getenv", "not-set", "none",
)

# Exact matched strings vetted as false positives (never put real keys here).
ALLOWLIST: set[str] = set()

SKIP_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".ico", ".woff", ".woff2", ".pdf", ".zip"}


def is_placeholder(value: str) -> bool:
    low = value.lower()
    return any(m in low for m in PLACEHOLDER_MARKERS)


def tracked_files() -> list[Path]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [REPO_ROOT / line for line in out.splitlines() if line.strip()]


def scan_file(path: Path) -> list[str]:
    if path.suffix.lower() in SKIP_SUFFIXES or not path.is_file():
        return []
    try:
        text = path.read_text(encoding="utf-8", errors="strict")
    except (UnicodeDecodeError, OSError):
        return []
    findings: list[str] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        for name, pattern in PATTERNS:
            for match in pattern.finditer(line):
                value = match.group(1) if name == "generic assignment" else match.group(0)
                if value in ALLOWLIST or is_placeholder(value):
                    continue
                findings.append(f"{path.relative_to(REPO_ROOT)}:{lineno}: {name}")
    return findings


def main() -> int:
    files = tracked_files()
    findings: list[str] = []
    for path in files:
        findings.extend(scan_file(path))
    if findings:
        print("SECRET SCAN FAILED — possible credentials in tracked files:")
        for f in findings:
            print(f"  {f}")
        print("\nRemove the secret, rotate it if it was ever committed, and re-run.")
        return 1
    print(f"secret scan OK ({len(files)} tracked files checked)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
