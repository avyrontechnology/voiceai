"""Tests for voiceai.core.environment (US1, task T009).

The environment module is the ONLY place os.environ is read
(Constitution V). Typed accessors with ConfigurationError on misuse.
"""

import pytest

from voiceai.core.environment import (
    get_batch_max_entries,
    get_bool,
    get_campaign_max_entries,
    get_cookie_samesite,
    get_cookie_secure,
    get_float,
    get_int,
    get_mongo_db,
    get_mongo_url,
    get_redis_url,
    get_str,
    get_warm_pool_enabled,
    redact,
    require_str,
)
from voiceai.errors import ConfigurationError


def test_get_str_returns_default_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing keys fall back to the default instead of raising."""
    monkeypatch.delenv("VOICEAI_TEST_MISSING_KEY", raising=False)
    assert get_str("VOICEAI_TEST_MISSING_KEY", "fallback") == "fallback"
    assert get_str("VOICEAI_TEST_MISSING_KEY") is None


def test_get_str_returns_set_value(monkeypatch: pytest.MonkeyPatch) -> None:
    """Present keys return their raw string value."""
    monkeypatch.setenv("VOICEAI_TEST_KEY", "hello")
    assert get_str("VOICEAI_TEST_KEY") == "hello"


def test_require_str_raises_configuration_error_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Required-but-absent config raises ConfigurationError, never KeyError."""
    monkeypatch.delenv("VOICEAI_TEST_REQUIRED", raising=False)
    with pytest.raises(ConfigurationError):
        require_str("VOICEAI_TEST_REQUIRED")


def test_require_str_rejects_blank_values(monkeypatch: pytest.MonkeyPatch) -> None:
    """Blank strings are treated as missing for required keys."""
    monkeypatch.setenv("VOICEAI_TEST_REQUIRED", "   ")
    with pytest.raises(ConfigurationError):
        require_str("VOICEAI_TEST_REQUIRED")


def test_get_int_parses_and_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Integers parse; unparsable values raise ConfigurationError."""
    monkeypatch.setenv("VOICEAI_TEST_INT", "42")
    assert get_int("VOICEAI_TEST_INT", 0) == 42
    monkeypatch.setenv("VOICEAI_TEST_INT", "not-a-number")
    with pytest.raises(ConfigurationError):
        get_int("VOICEAI_TEST_INT", 0)
    monkeypatch.delenv("VOICEAI_TEST_INT")
    assert get_int("VOICEAI_TEST_INT", 7) == 7


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("no", False),
        ("off", False),
        ("", False),
    ],
)
def test_get_bool_parses_common_spellings(monkeypatch: pytest.MonkeyPatch, raw: str, expected: bool) -> None:
    """Boolean parsing accepts the common truthy/falsy spellings."""
    monkeypatch.setenv("VOICEAI_TEST_BOOL", raw)
    assert get_bool("VOICEAI_TEST_BOOL", not expected) is expected


def test_get_bool_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unrecognized boolean spellings raise ConfigurationError."""
    monkeypatch.setenv("VOICEAI_TEST_BOOL", "maybe")
    with pytest.raises(ConfigurationError):
        get_bool("VOICEAI_TEST_BOOL", False)


def test_get_float_parses_and_rejects_garbage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Floats parse; unparsable values raise ConfigurationError."""
    monkeypatch.setenv("VOICEAI_TEST_FLOAT", "1.5")
    assert get_float("VOICEAI_TEST_FLOAT", 0.0) == 1.5
    monkeypatch.setenv("VOICEAI_TEST_FLOAT", "not-a-float")
    with pytest.raises(ConfigurationError):
        get_float("VOICEAI_TEST_FLOAT", 0.0)
    monkeypatch.delenv("VOICEAI_TEST_FLOAT")
    assert get_float("VOICEAI_TEST_FLOAT", 2.5) == 2.5


def test_entry_cap_accessors_floor_at_one(monkeypatch: pytest.MonkeyPatch) -> None:
    """Batch/campaign caps honor env overrides but never drop below 1."""
    for key in ("BATCH_MAX_ENTRIES", "CAMPAIGN_MAX_ENTRIES"):
        monkeypatch.delenv(key, raising=False)
    assert get_batch_max_entries(100) == 100
    assert get_campaign_max_entries(100) == 100
    monkeypatch.setenv("BATCH_MAX_ENTRIES", "10")
    assert get_batch_max_entries(100) == 10
    monkeypatch.setenv("BATCH_MAX_ENTRIES", "0")
    assert get_batch_max_entries(100) == 1
    monkeypatch.setenv("CAMPAIGN_MAX_ENTRIES", "not-a-number")
    with pytest.raises(ConfigurationError):
        get_campaign_max_entries(100)


def test_cookie_and_pool_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """Cookie and warm-pool flags parse with safe defaults."""
    for key in ("COOKIE_SECURE", "COOKIE_SAMESITE", "WARM_POOL_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    assert get_cookie_secure() is False
    assert get_cookie_samesite() == "lax"
    assert get_warm_pool_enabled() is False
    monkeypatch.setenv("COOKIE_SECURE", "1")
    monkeypatch.setenv("WARM_POOL_ENABLED", "true")
    assert get_cookie_secure() is True
    assert get_warm_pool_enabled() is True


def test_connection_accessors_have_local_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """Redis/Mongo accessors work with zero configuration (local dev)."""
    for key in ("REDIS_URL", "MONGO_URL", "MONGO_DB", "MONGO_USER", "MONGO_HOST", "MONGO_PASSWORD"):
        monkeypatch.delenv(key, raising=False)
    assert get_redis_url().startswith("redis://")
    assert get_mongo_url().startswith("mongodb://")
    assert get_mongo_db() != ""


def test_mongo_url_explicit_wins_over_parts(monkeypatch: pytest.MonkeyPatch) -> None:
    """Explicit MONGO_URL beats Atlas parts; parts beat the default."""
    monkeypatch.setenv("MONGO_URL", "mongodb://explicit:27017")
    monkeypatch.setenv("MONGO_USER", "u")
    monkeypatch.setenv("MONGO_HOST", "h.example")
    assert get_mongo_url() == "mongodb://explicit:27017"
    monkeypatch.delenv("MONGO_URL")
    url = get_mongo_url()
    assert url.startswith("mongodb+srv://u:") and "@h.example/" in url


def test_redact_masks_secrets() -> None:
    """Redaction never leaks the full secret value."""
    secret = "sk-live-abcdef123456"
    masked = redact(secret)
    assert secret not in masked
    assert masked != ""
    assert redact("") == ""
    assert redact(None) == ""  # type: ignore[arg-type]
