"""Process configuration (AGENTS.md rule 4): every environment variable is declared here.

`os.getenv` anywhere else in the new architecture is a violation — config flows env-file →
`Environment` → container → constructors, so a value is validated exactly once and every
consumer receives it through its constructor instead of reaching for the process environment.
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any, Final, Literal
from urllib.parse import urlsplit

from dotenv import load_dotenv
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator, model_validator

from voiceai.common.constants import DEFAULT_LOG_LEVEL, SAFE_URL_SCHEMES
from voiceai.common.errors import ConfigurationError

__all__ = [
    "APP_ENV_DEV",
    "APP_ENV_PROD",
    "APP_ENV_STAGING",
    "DB_BACKEND_MEMORY",
    "DB_BACKEND_MONGO",
    "Environment",
    "ensure_exact_origins",
    "get_environment",
    "load_environment",
    "reset_environment",
]

APP_ENV_DEV: Final = "dev"
APP_ENV_STAGING: Final = "staging"
APP_ENV_PROD: Final = "prod"
DB_BACKEND_MEMORY: Final = "memory"
DB_BACKEND_MONGO: Final = "mongo"
DEFAULT_DB_NAME: Final = "otoba"

_FIELD_ALLOWED_ORIGINS: Final[str] = "allowed_origins"
_FIELD_COOKIE_SECURE: Final[str] = "cookie_secure"
_FIELD_COOKIE_SAMESITE: Final[str] = "cookie_samesite"
_FIELD_JWT_PRIVATE_KEY: Final[str] = "jwt_private_key"
_FIELD_JWT_PUBLIC_KEY: Final[str] = "jwt_public_key"
_FIELD_JWT_ACCESS_TTL_S: Final[str] = "jwt_access_ttl_s"
_FIELD_JWT_REFRESH_TTL_S: Final[str] = "jwt_refresh_ttl_s"
_LIST_SEPARATOR: Final[str] = ","
_TRUE_VALUES: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES: Final[frozenset[str]] = frozenset({"0", "false", "no", "off"})
_INVALID_BOOL_MESSAGE: Final[str] = "{path} must be a boolean (true/false)"
_INVALID_VALUE_MESSAGE: Final[str] = "Invalid value for {path}"
_INVALID_ORIGIN_MESSAGE: Final[str] = (
    "{path} entries must be exact http(s) origins (scheme://host[:port], "
    "no wildcard, no path); invalid entry: {entry!r}"
)
_INVALID_JWT_KEYS_MESSAGE: Final[str] = (
    "{path} requires both JWT_PRIVATE_KEY and JWT_PUBLIC_KEY set (PEM text), or neither (JWT flows stay dark)"
)
_INVALID_JWT_TTL_MESSAGE: Final[str] = "{path} must be a positive number of seconds"
_INVALID_JWT_REFRESH_MESSAGE: Final[str] = "{path} must exceed JWT_ACCESS_TTL_S (refresh outlives access)"
_INVALID_SAMESITE_MESSAGE: Final[str] = (
    "{path} 'none' mandates the Secure cookie flag — set COOKIE_SECURE=1 (cross-site cookies are "
    "dropped by browsers without it)"
)
_ORIGIN_WILDCARD: Final[str] = "*"
_UNKNOWN_PATH: Final[str] = "UNKNOWN"

_dotenv_loaded: bool = False


class Environment(BaseModel):
    """The typed, validated view of the process environment.

    Field names are the lower_snake form of their variable names: `allowed_origins` is read
    from `ALLOWED_ORIGINS`. The model is frozen — configuration is read once at startup and is
    never mutated by request handling.
    """

    model_config = ConfigDict(frozen=True)

    app_env: Literal["dev", "staging", "prod"] = APP_ENV_DEV
    log_level: str = DEFAULT_LOG_LEVEL
    #: Empty disables every redis-backed feature (the container then registers `None`).
    #: Legacy single-URL knob: the isolated `redis_*_url` fields below fall back to it,
    #: so existing deployments keep working until they set three separate URLs.
    redis_url: str = ""
    #: Isolated Redis URLs (greenfield split): cache holds TTL-only ephemera (session
    #: cache, throttle counters, locks, pub/sub); broker/results back Celery. Each falls
    #: back to `redis_url` when empty. Production sets three SEPARATE URLs — Upstash has
    #: no `SELECT`, so isolation means separate databases, never DB indexes.
    redis_cache_url: str = ""
    redis_broker_url: str = ""
    redis_result_url: str = ""
    #: Base URL of the talko-service deployment the outbound bridge dials and
    #: fetches partner DIDs through (spec 0009). Empty means outbound Talko
    #: features resolve per-request/per-record values only, never a default.
    talko_service_base_url: str = ""
    db_backend: Literal["memory", "mongo"] = DB_BACKEND_MEMORY
    db_url: str = ""
    #: Preferred MongoDB connection string (Atlas `mongodb+srv://...`). Wins over `db_url`
    #: when set; the T7 cutover makes this canonical and `db_url` the fallback.
    mongo_url: str = ""
    db_name: str = DEFAULT_DB_NAME
    #: `ALLOWED_ORIGINS` as a comma-separated list of exact `scheme://host[:port]` origins.
    #: Wildcards and non-origin entries are rejected by the model validator on EVERY
    #: construction path (not just the env loader), and the field is an immutable tuple so a
    #: held reference cannot widen the allowlist in place. Empty means CORS is not installed.
    allowed_origins: tuple[str, ...] = ()
    #: Tri-state: `True`/`False` force the cookie flag, `None` defers to `app_env`.
    cookie_secure: bool | None = None
    #: Cookie `Domain` (empty = host-only, the current behavior) and `SameSite` mode.
    #: `none` mandates `Secure` — enforced by the model validator (fail closed), because
    #: browsers drop cross-site cookies without it (the Vercel→droplet 401 loop).
    cookie_domain: str = ""
    cookie_samesite: Literal["lax", "strict", "none"] = "lax"
    #: Stateless JWT sessions (CTO decision): short-lived access tokens plus opaque
    #: rotating refresh tokens. Keys are PEM text; both set or both empty (fail closed) —
    #: empty keeps every JWT flow dark until the auth turn lands.
    jwt_issuer: str = "otobaai"
    jwt_audience: str = "otobaai-api"
    jwt_access_ttl_s: int = 900
    jwt_refresh_ttl_s: int = 30 * 24 * 3600
    jwt_private_key: str = ""
    jwt_public_key: str = ""
    #: Blob store for prompts/audio/KB binaries (replaces CWD-relative `agent_data/`).
    #: Empty disables it; the voice/agents turns wire the uploader.
    blob_store_url: str = ""
    blob_bucket: str = ""
    blob_region: str = "ap-south-1"
    #: Cutover flag for the new-architecture voice WS route (spec 0004 B14): `False`
    #: (default) keeps ``/chat/v1/{agent_id}`` dark — the handler closes immediately —
    #: while quickstart stays the deployed entry. ``VOICE_WS_ENABLED`` in the environment.
    voice_ws_enabled: bool = False

    @field_validator(_FIELD_ALLOWED_ORIGINS)
    @classmethod
    def _validated_origins(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        """Reject wildcard/non-origin allowlist entries on every construction path.

        Args:
            value: The coerced tuple of origin entries.

        Returns:
            The tuple unchanged when every entry is an exact origin.

        Raises:
            ConfigurationError: On any entry that is not an exact `http(s)://host[:port]`
                origin — pydantic propagates it unwrapped, so direct `Environment(...)`
                construction fails exactly like the env-loader path.
        """
        ensure_exact_origins(value)
        return value

    @model_validator(mode="after")
    def _validate_cross_field(self) -> Environment:
        """Fail closed on insecure or half-configured combinations.

        Returns:
            The validated environment unchanged.

        Raises:
            ConfigurationError: When `SameSite=None` lacks the `Secure` flag, when
                exactly one JWT key is set, or when the JWT TTLs are non-positive or
                inverted. Raised directly (not wrapped): like the origins validator,
                callers on every construction path must see the offending variable.
        """
        if self.cookie_samesite == "none" and self.cookie_secure is False:
            raise ConfigurationError(
                _INVALID_SAMESITE_MESSAGE.format(path=_FIELD_COOKIE_SAMESITE.upper()),
                path=_FIELD_COOKIE_SAMESITE.upper(),
            )
        private_set = bool(self.jwt_private_key)
        public_set = bool(self.jwt_public_key)
        if private_set != public_set:
            raise ConfigurationError(
                _INVALID_JWT_KEYS_MESSAGE.format(path=_FIELD_JWT_PRIVATE_KEY.upper()),
                path=_FIELD_JWT_PRIVATE_KEY.upper(),
            )
        if private_set:
            if self.jwt_access_ttl_s <= 0:
                raise ConfigurationError(
                    _INVALID_JWT_TTL_MESSAGE.format(path=_FIELD_JWT_ACCESS_TTL_S.upper()),
                    path=_FIELD_JWT_ACCESS_TTL_S.upper(),
                )
            if self.jwt_refresh_ttl_s <= self.jwt_access_ttl_s:
                raise ConfigurationError(
                    _INVALID_JWT_REFRESH_MESSAGE.format(path=_FIELD_JWT_REFRESH_TTL_S.upper()),
                    path=_FIELD_JWT_REFRESH_TTL_S.upper(),
                )
        return self

    @property
    def is_prod(self) -> bool:
        """Report whether this process runs in production.

        Returns:
            `True` when `APP_ENV` is `prod`.
        """
        return self.app_env == APP_ENV_PROD

    @property
    def cookie_secure_effective(self) -> bool:
        """Resolve the `Secure` cookie flag actually used when setting cookies.

        Returns:
            The explicit `COOKIE_SECURE` when set, otherwise `True` in production only — local
            HTTP development would break on `Secure` cookies, production must not skip them.
        """
        if self.cookie_secure is not None:
            return self.cookie_secure
        return self.is_prod

    @property
    def redis_cache_url_effective(self) -> str:
        """Resolve the cache Redis URL, falling back to the legacy single URL.

        Returns:
            `REDIS_CACHE_URL` when set, otherwise `REDIS_URL` (empty disables cache features).
        """
        return self.redis_cache_url or self.redis_url

    @property
    def redis_broker_url_effective(self) -> str:
        """Resolve the Celery broker URL, falling back to the legacy single URL.

        Returns:
            `REDIS_BROKER_URL` when set, otherwise `REDIS_URL` (empty disables the broker).
        """
        return self.redis_broker_url or self.redis_url

    @property
    def redis_result_url_effective(self) -> str:
        """Resolve the Celery result-backend URL, falling back to the broker URL.

        Returns:
            `REDIS_RESULT_URL` when set, otherwise the effective broker URL.
        """
        return self.redis_result_url or self.redis_broker_url_effective

    @property
    def db_url_effective(self) -> str:
        """Resolve the MongoDB connection string, preferring the Atlas alias.

        Returns:
            `MONGO_URL` when set, otherwise `DB_URL` (empty fails closed for `mongo` backend).
        """
        return self.mongo_url or self.db_url

    @property
    def jwt_enabled(self) -> bool:
        """Report whether the JWT session flows may run.

        Returns:
            `True` only when both keys are configured — a half-configured keypair fails
            closed at validation, so this is purely a darkness check for the auth turn.
        """
        return bool(self.jwt_private_key and self.jwt_public_key)


def _split_csv(raw: str) -> list[str]:
    """Split a comma-separated environment value into a clean list.

    Args:
        raw: The raw variable value.

    Returns:
        Trimmed entries, with empties dropped so a trailing comma is harmless.
    """
    return [item.strip() for item in raw.split(_LIST_SEPARATOR) if item.strip()]


def _is_exact_origin(entry: str) -> bool:
    """Report whether one `ALLOWED_ORIGINS` entry is an exact web origin.

    An origin is `scheme://host[:port]` and nothing else: a wildcard anywhere in the entry, a
    non-http(s) scheme, userinfo, a path, a query or a fragment all disqualify it — with
    `allow_credentials=True` the CORS allowlist must never be broader than what was written.

    Args:
        entry: One trimmed entry from the comma-separated variable.

    Returns:
        `True` only for an exact `http(s)://host[:port]` origin.
    """
    if _ORIGIN_WILDCARD in entry:
        return False
    try:
        parsed = urlsplit(entry)
        return (
            parsed.scheme in SAFE_URL_SCHEMES
            and bool(parsed.hostname)
            and parsed.username is None
            and parsed.password is None
            # Reading `.port` raises ValueError on a non-numeric or out-of-range port.
            and (parsed.port is None or parsed.port >= 0)
            and not parsed.path
            and not parsed.query
            and not parsed.fragment
        )
    except ValueError:  # a broken IPv6 literal or a bad port is not an origin
        return False


def ensure_exact_origins(origins: Sequence[str]) -> None:
    """Validate that every CORS allowlist entry is an exact web origin.

    Shared by the `Environment` model validator and `_add_cors`'s defense-in-depth re-check —
    the latter also covers `model_copy(update=...)`, a pydantic path that skips validators.

    Args:
        origins: Candidate origin entries.

    Raises:
        ConfigurationError: On the first entry that is not an exact `http(s)://host[:port]`
            origin. The process refuses to start rather than serve credentialed CORS with a
            lax allowlist.
    """
    path = _FIELD_ALLOWED_ORIGINS.upper()
    for entry in origins:
        if not _is_exact_origin(entry):
            raise ConfigurationError(_INVALID_ORIGIN_MESSAGE.format(path=path, entry=entry), path=path)


def _parse_allowed_origins(raw: str) -> list[str]:
    """Split the `ALLOWED_ORIGINS` variable; validation happens on the model itself.

    Args:
        raw: The raw comma-separated variable value.

    Returns:
        The trimmed entries (empty disables CORS entirely); the `Environment` field validator
        rejects anything that is not an exact origin.
    """
    return _split_csv(raw)


def _parse_tristate_bool(path: str, raw: str) -> bool | None:
    """Parse a boolean environment value, treating an empty value as "unset".

    Args:
        path: The variable name, used in the error message.
        raw: The raw variable value.

    Returns:
        `True`/`False`, or `None` when the variable is present but empty (common in generated
        compose files) so the model default applies.

    Raises:
        ConfigurationError: When the value is neither truthy nor falsy.
    """
    normalized = raw.strip().lower()
    if not normalized:
        return None
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    raise ConfigurationError(_INVALID_BOOL_MESSAGE.format(path=path), path=path)


def _parse_samesite(raw: str) -> str:
    """Normalise the `COOKIE_SAMESITE` value; an empty value means "unset".

    Args:
        raw: The raw variable value.

    Returns:
        The lower-cased mode, or `"lax"` when the variable is present but empty (the
        safe default, mirroring the field default).
    """
    normalized = raw.strip().lower()
    return normalized or "lax"


def _parse_pem(raw: str) -> str:
    """Normalise a PEM variable for runtimes that cannot carry real newlines.

    Docker `--env-file`/`env_file` entries are single-line literals, so operators
    paste keys with `\\n` escapes; local shells and python-dotenv carry real
    newlines. Accepting both keeps one documented format per file. PEM base64
    never contains a backslash, so the replacement cannot corrupt a real key.

    Args:
        raw: The raw variable value.

    Returns:
        The value with literal `\\n` sequences decoded to newlines.
    """
    return raw.replace("\\n", "\n")


def _parse_raw(field_name: str, raw: str) -> Any:  # why: each field parses to its own type
    """Convert one raw environment value into the type its field expects.

    Args:
        field_name: The `Environment` field being populated.
        raw: The raw variable value.

    Returns:
        The parsed value; plain strings and literals pass through for pydantic to validate.
    """
    if field_name == _FIELD_ALLOWED_ORIGINS:
        return _parse_allowed_origins(raw)
    if field_name == _FIELD_COOKIE_SECURE:
        return _parse_tristate_bool(field_name.upper(), raw)
    if field_name == _FIELD_COOKIE_SAMESITE:
        return _parse_samesite(raw)
    if field_name in (_FIELD_JWT_PRIVATE_KEY, _FIELD_JWT_PUBLIC_KEY):
        return _parse_pem(raw)
    return raw


def _collect_values() -> dict[str, Any]:  # why: values are per-field typed
    """Read every declared variable from the process environment.

    Returns:
        A mapping of field name to parsed value, containing only variables that are set — an
        absent variable must fall through to the field's declared default.
    """
    values: dict[str, Any] = {}  # why: see return type
    for field_name in Environment.model_fields:
        raw = os.getenv(field_name.upper())
        if raw is not None:
            values[field_name] = _parse_raw(field_name, raw)
    return values


def _first_error_path(exc: ValidationError) -> str:
    """Name the variable a validation error points at.

    Args:
        exc: The pydantic failure raised while building `Environment`.

    Returns:
        The offending variable name in upper case, or `UNKNOWN` when the error has no location.
    """
    for error in exc.errors():
        location = error["loc"]
        if location:
            return str(location[0]).upper()
    return _UNKNOWN_PATH


def _load_dotenv_once(dotenv_path: str | Path | None) -> None:
    """Populate the process environment from a `.env` file.

    The default file is loaded at most once per process; an explicit path is always loaded so
    tests can point at a fixture (or at a file that does not exist, for hermetic runs). Real
    environment variables always win (`override=False`).

    Args:
        dotenv_path: Explicit env-file path, or `None` for the default lookup.
    """
    global _dotenv_loaded
    if dotenv_path is not None:
        load_dotenv(dotenv_path=str(dotenv_path), override=False)
        return
    if _dotenv_loaded:
        return
    load_dotenv(override=False)
    _dotenv_loaded = True


def load_environment(dotenv_path: str | Path | None = None) -> Environment:
    """Build an `Environment` from the process environment (and an optional `.env`).

    Args:
        dotenv_path: Explicit env-file path; `None` uses python-dotenv's default lookup.

    Returns:
        The validated configuration.

    Raises:
        ConfigurationError: When a variable holds a value the model rejects. The offending
            variable name is carried in `details["path"]` so the operator sees what to fix.
    """
    _load_dotenv_once(dotenv_path)
    values = _collect_values()
    try:
        return Environment(**values)
    except ValidationError as exc:
        path = _first_error_path(exc)
        raise ConfigurationError(_INVALID_VALUE_MESSAGE.format(path=path), path=path, cause=exc) from exc


@lru_cache(maxsize=1)
def get_environment() -> Environment:
    """Return the process-wide configuration, loading it on first use.

    Returns:
        The cached `Environment`. Tests call `reset_environment()` to drop the cache; app and
        container factories accept an explicit `Environment` so they never depend on it.
    """
    return load_environment()


def reset_environment() -> None:
    """Drop the cached environment (and the "dotenv already loaded" latch).

    Exists for tests: every arch test resets the cache so one test's `monkeypatch.setenv` can
    never leak into the next one's configuration.
    """
    global _dotenv_loaded
    get_environment.cache_clear()
    _dotenv_loaded = False
