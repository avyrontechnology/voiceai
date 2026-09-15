"""Central environment access (Constitution V).

This module is the ONLY place in ``voiceai/`` where ``os.environ`` is
read. All other code obtains configuration through these typed accessors,
which are injected (values or callables) via ``core.container`` — never by
importing ``os`` directly.

Secrets passing through here MUST be redacted before logging (see
:func:`redact`).
"""

from __future__ import annotations

import os
from typing import Optional

from dotenv import load_dotenv

from voiceai.core.exceptions import ConfigurationError

load_dotenv()  # No-op when no .env file is present; explicit env always wins.

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", ""})

_DEFAULT_REDIS_URL = "redis://localhost:6379"
_DEFAULT_MONGO_URL = "mongodb://localhost:27017"
_DEFAULT_MONGO_DB = "voiceai"


def get_str(key: str, default: Optional[str] = None) -> Optional[str]:
    """Return the raw string value of ``key`` or ``default`` when absent.

    Args:
        key: Environment variable name.
        default: Fallback when the variable is not set.

    Returns:
        The variable value, or ``default``.
    """
    return os.environ.get(key, default)


def require_str(key: str) -> str:
    """Return the value of ``key``; raise when missing or blank.

    Args:
        key: Required environment variable name.

    Returns:
        The non-blank variable value.

    Raises:
        ConfigurationError: If the variable is unset or blank.
    """
    value = os.environ.get(key, "")
    if not value.strip():
        raise ConfigurationError(f"missing required environment variable: {key}", path=key)
    return value


def get_int(key: str, default: int) -> int:
    """Return ``key`` parsed as an integer.

    Args:
        key: Environment variable name.
        default: Fallback when the variable is not set.

    Returns:
        The parsed integer or ``default``.

    Raises:
        ConfigurationError: If the value is set but not an integer.
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(f"environment variable {key} must be an integer, got {raw!r}", path=key) from exc


def get_bool(key: str, default: bool) -> bool:
    """Return ``key`` parsed as a boolean.

    Accepts ``1/true/yes/on`` (any case) as true and
    ``0/false/no/off``/empty as false.

    Args:
        key: Environment variable name.
        default: Fallback when the variable is not set.

    Returns:
        The parsed boolean or ``default``.

    Raises:
        ConfigurationError: If the value is set but unrecognized.
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigurationError(f"environment variable {key} must be a boolean, got {raw!r}", path=key)


def get_float(key: str, default: float) -> float:
    """Return ``key`` parsed as a float.

    Args:
        key: Environment variable name.
        default: Fallback when the variable is not set.

    Returns:
        The parsed float or ``default``.

    Raises:
        ConfigurationError: If the value is set but not a float.
    """
    raw = os.environ.get(key)
    if raw is None:
        return default
    try:
        return float(raw.strip())
    except ValueError as exc:
        raise ConfigurationError(f"environment variable {key} must be a float, got {raw!r}", path=key) from exc


def get_redis_url() -> str:
    """Return the Redis URL.

    Returns:
        ``REDIS_URL`` or the local-dev default.
    """
    return get_str("REDIS_URL", _DEFAULT_REDIS_URL) or _DEFAULT_REDIS_URL


def get_mongo_url() -> str:
    """Return the MongoDB connection URL.

    Precedence: explicit ``MONGO_URL`` wins; else an Atlas URL is built
    from ``MONGO_USER``/``MONGO_PASSWORD``/``MONGO_HOST`` (+``MONGO_DB``)
    when those are set; else the local-dev default.

    Returns:
        The MongoDB connection URL.
    """
    explicit = get_str("MONGO_URL")
    if explicit:
        return explicit
    user = get_str("MONGO_USER")
    host = get_str("MONGO_HOST")
    if user and host:
        from urllib.parse import quote_plus

        password = get_str("MONGO_PASSWORD", "") or ""
        auth = f"{quote_plus(user)}:{quote_plus(password)}@" if password else f"{quote_plus(user)}@"
        return f"mongodb+srv://{auth}{host}/{get_mongo_db()}?retryWrites=true&w=majority"
    return _DEFAULT_MONGO_URL


def get_mongo_db() -> str:
    """Return the MongoDB database name.

    Returns:
        ``MONGO_DB`` or the default database name.
    """
    return get_str("MONGO_DB", _DEFAULT_MONGO_DB) or _DEFAULT_MONGO_DB


def redact(value: Optional[str], *, keep_tail: int = 4) -> str:
    """Mask a secret for safe display (logs, errors, health output).

    Args:
        value: The secret value; ``None``/empty yields ``""``.
        keep_tail: How many trailing characters to reveal (0 hides all).

    Returns:
        The masked value; never the full secret.
    """
    if not value:
        return ""
    if keep_tail <= 0 or len(value) <= keep_tail:
        return "*" * 8
    return "*" * 8 + value[-keep_tail:]


def get_batch_max_entries(fallback: int) -> int:
    """Return the batch entry cap, floored at 1.

    Args:
        fallback: Cap when ``BATCH_MAX_ENTRIES`` is unset.

    Returns:
        The effective cap (never below 1).

    Raises:
        ConfigurationError: If the value is set but not an integer.
    """
    return max(1, get_int("BATCH_MAX_ENTRIES", fallback))


def get_campaign_max_entries(fallback: int) -> int:
    """Return the campaign entry cap, floored at 1.

    Args:
        fallback: Cap when ``CAMPAIGN_MAX_ENTRIES`` is unset.

    Returns:
        The effective cap (never below 1).

    Raises:
        ConfigurationError: If the value is set but not an integer.
    """
    return max(1, get_int("CAMPAIGN_MAX_ENTRIES", fallback))


def get_cookie_secure() -> bool:
    """Return whether auth cookies require Secure transport.

    Returns:
        ``COOKIE_SECURE`` as a boolean (default False).
    """
    return get_bool("COOKIE_SECURE", False)


def get_cookie_samesite() -> str:
    """Return the SameSite attribute for auth cookies.

    Returns:
        ``COOKIE_SAMESITE`` (default ``"lax"``).
    """
    return get_str("COOKIE_SAMESITE", "lax") or "lax"


def get_warm_pool_enabled() -> bool:
    """Return whether the TTS warm pool is enabled.

    Returns:
        ``WARM_POOL_ENABLED`` as a boolean (default False).
    """
    return get_bool("WARM_POOL_ENABLED", False)


def set_env(key: str, value: str) -> None:
    """Write one process-environment value (the only sanctioned writer).

    Writes are rare (handoff to child components that live-read config);
    prefer passing values explicitly. Reads still go through the getters.

    Args:
        key: Environment variable name.
        value: Value to set.
    """
    os.environ[key] = value


_STORE_BACKENDS = frozenset({"memory", "redis", "mongo"})


def get_platform_store_backend() -> str:
    """Return the selected platform persistence backend.

    Returns:
        ``PLATFORM_STORE_BACKEND`` one of memory/redis/mongo (default memory).

    Raises:
        ConfigurationError: If set to an unknown backend.
    """
    raw = (get_str("PLATFORM_STORE_BACKEND", "memory") or "memory").strip().lower()
    if raw not in _STORE_BACKENDS:
        raise ConfigurationError(
            f"unknown PLATFORM_STORE_BACKEND {raw!r} (expected one of: {', '.join(sorted(_STORE_BACKENDS))})",
            path="PLATFORM_STORE_BACKEND",
        )
    return raw
