"""Security: startup key validation + log redaction.

Rules (docs/SECURITY.md):
- DASHSCOPE_API_KEY is required at startup unless GOVTRANS_ALLOW_MISSING_KEYS
  is explicitly set for UI-only local development.
- The key must never reach logs, API responses, or the browser.
"""
from __future__ import annotations

import logging
import re
from typing import Iterable

from .config import Settings

logger = logging.getLogger("govtrans.security")

# Generic secret shapes that must never be logged even if config is wrong.
GENERIC_SECRET_PATTERNS = [
    re.compile(r"sk-[A-Za-z0-9]{8,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"Bearer\s+[A-Za-z0-9._\-]{12,}", re.IGNORECASE),
]

REDACTED = "[REDACTED]"


class SecretRedactionFilter(logging.Filter):
    """Logging filter that scrubs known secrets and generic key shapes."""

    def __init__(self, secrets: Iterable[str] = ()) -> None:
        super().__init__()
        self._secrets = [s for s in secrets if s]

    def update_secrets(self, secrets: Iterable[str]) -> None:
        self._secrets = [s for s in secrets if s]

    def _scrub(self, text: str) -> str:
        for secret in self._secrets:
            if secret and secret in text:
                text = text.replace(secret, REDACTED)
        for pattern in GENERIC_SECRET_PATTERNS:
            text = pattern.sub(REDACTED, text)
        return text

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = self._scrub(str(record.msg))
        if record.args:
            record.args = tuple(
                self._scrub(str(a)) if isinstance(a, str) else a for a in record.args
            )
        return True


def install_log_redaction(settings: Settings) -> SecretRedactionFilter:
    secrets = []
    if settings.dashscope_api_key:
        secrets.append(settings.dashscope_api_key.get_secret_value())
    if settings.s3_secret_key:
        secrets.append(settings.s3_secret_key.get_secret_value())
    filt = SecretRedactionFilter(secrets)
    root = logging.getLogger()
    for handler in root.handlers or [logging.StreamHandler()]:
        if not root.handlers:
            root.addHandler(handler)
        handler.addFilter(filt)
    return filt


def validate_required_keys(settings: Settings) -> list[str]:
    """Fail-fast startup validation. Returns list of missing key names."""
    missing: list[str] = []
    if not settings.dashscope_api_key or not settings.dashscope_api_key.get_secret_value():
        missing.append("DASHSCOPE_API_KEY")
    if missing:
        if settings.govtrans_allow_missing_keys and not settings.is_production:
            logger.warning(
                "startup: missing required keys %s — continuing because "
                "GOVTRANS_ALLOW_MISSING_KEYS=true (development only). LLM calls will fail.",
                missing,
            )
            return missing
        raise RuntimeError(
            f"Missing required environment keys: {', '.join(missing)}. "
            "Copy .env.example to .env and fill real values. See docs/SECURITY.md."
        )
    return missing
