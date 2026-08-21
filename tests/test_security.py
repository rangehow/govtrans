import logging

import pytest

from apps.api.config import Settings
from apps.api.security import SecretRedactionFilter, validate_required_keys

pytestmark = pytest.mark.unit

FAKE_KEY = "sk-test0000000000000000deadbeef"  # placeholder-shaped, not a real key


class TestKeyValidation:
    def test_missing_key_raises_in_production(self):
        settings = Settings(
            govtrans_env="production", dashscope_api_key=None,
            govtrans_allow_missing_keys=False, _env_file=None,
        )
        with pytest.raises(RuntimeError, match="DASHSCOPE_API_KEY"):
            validate_required_keys(settings)

    def test_missing_key_blocked_without_escape_hatch(self):
        settings = Settings(
            govtrans_env="development", dashscope_api_key=None,
            govtrans_allow_missing_keys=False, _env_file=None,
        )
        with pytest.raises(RuntimeError):
            validate_required_keys(settings)

    def test_escape_hatch_allows_dev_only(self):
        settings = Settings(
            govtrans_env="development", dashscope_api_key=None,
            govtrans_allow_missing_keys=True, _env_file=None,
        )
        assert validate_required_keys(settings) == ["DASHSCOPE_API_KEY"]

    def test_escape_hatch_never_works_in_production(self):
        settings = Settings(
            govtrans_env="production", dashscope_api_key=None,
            govtrans_allow_missing_keys=True, _env_file=None,
        )
        with pytest.raises(RuntimeError):
            validate_required_keys(settings)


class TestRedaction:
    def test_configured_secret_scrubbed(self):
        filt = SecretRedactionFilter([FAKE_KEY])
        record = logging.LogRecord(
            "t", logging.INFO, __file__, 1, f"calling provider with {FAKE_KEY}", (), None
        )
        filt.filter(record)
        assert FAKE_KEY not in record.msg
        assert "[REDACTED]" in record.msg

    def test_generic_sk_pattern_scrubbed(self):
        filt = SecretRedactionFilter([])
        record = logging.LogRecord(
            "t", logging.INFO, __file__, 1,
            "Authorization: Bearer abcdef1234567890XYZ", (), None,
        )
        filt.filter(record)
        assert "abcdef1234567890XYZ" not in record.msg
