"""Environment parsing: defaults, list/tri-state handling, and configuration failures.

Every test deletes the variables it cares about and points `load_environment` at a file that
does not exist, so a developer's own `.env` can never change a result.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError

from voiceai.common.errors import ConfigurationError
from voiceai.core.environment import (
    Environment,
    get_environment,
    load_environment,
    reset_environment,
)

ENV_VARS = (
    "APP_ENV",
    "LOG_LEVEL",
    "REDIS_URL",
    "REDIS_CACHE_URL",
    "REDIS_BROKER_URL",
    "REDIS_RESULT_URL",
    "DB_BACKEND",
    "DB_URL",
    "DB_NAME",
    "MONGO_URL",
    "ALLOWED_ORIGINS",
    "COOKIE_SECURE",
    "COOKIE_SAMESITE",
    "COOKIE_DOMAIN",
    "JWT_ISSUER",
    "JWT_AUDIENCE",
    "JWT_ACCESS_TTL_S",
    "JWT_REFRESH_TTL_S",
    "JWT_PRIVATE_KEY",
    "JWT_PUBLIC_KEY",
    "BLOB_STORE_URL",
    "BLOB_BUCKET",
    "BLOB_REGION",
    "TALKO_SERVICE_BASE_URL",
    "VOICE_WS_ENABLED",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test from a process environment with none of our variables set."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def absent_dotenv(tmp_path: Path) -> Path:
    """A path to an env file that does not exist — the hermetic default for these tests."""
    return tmp_path / "absent.env"


class TestDefaults:
    """What the process looks like with nothing configured."""

    def test_every_field_has_a_safe_default(self, absent_dotenv: Path) -> None:
        env = load_environment(absent_dotenv)
        assert env.app_env == "dev"
        assert env.log_level == "INFO"
        assert env.redis_url == ""
        assert env.db_backend == "memory"
        assert env.db_url == ""
        assert env.db_name == "otoba"
        assert env.allowed_origins == ()
        assert env.cookie_secure is None

    def test_defaults_are_not_production(self, absent_dotenv: Path) -> None:
        env = load_environment(absent_dotenv)
        assert env.is_prod is False
        assert env.cookie_secure_effective is False

    def test_configuration_is_immutable(self, absent_dotenv: Path) -> None:
        env = load_environment(absent_dotenv)
        with pytest.raises(ValidationError):
            env.app_env = "prod"


class TestScalarParsing:
    """Plain string and literal variables."""

    def test_app_env_is_read(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("APP_ENV", "prod")
        env = load_environment(absent_dotenv)
        assert (env.app_env, env.is_prod) == ("prod", True)

    def test_other_scalars_are_read(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("LOG_LEVEL", "DEBUG")
        monkeypatch.setenv("REDIS_URL", "redis://localhost:6379/0")
        monkeypatch.setenv("DB_NAME", "otoba_prod")
        env = load_environment(absent_dotenv)
        assert (env.log_level, env.redis_url, env.db_name) == ("DEBUG", "redis://localhost:6379/0", "otoba_prod")

    def test_db_url_passes_through_unchanged(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("DB_URL", "mongodb://db.internal:27017/otoba?replicaSet=rs0")
        assert load_environment(absent_dotenv).db_url == "mongodb://db.internal:27017/otoba?replicaSet=rs0"

    def test_mongo_backend_is_accepted_by_the_model(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("DB_BACKEND", "mongo")
        assert load_environment(absent_dotenv).db_backend == "mongo"

    def test_unknown_app_env_names_the_offending_variable(
        self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path
    ) -> None:
        monkeypatch.setenv("APP_ENV", "production")
        with pytest.raises(ConfigurationError) as raised:
            load_environment(absent_dotenv)
        assert raised.value.details["path"] == "APP_ENV"
        assert "APP_ENV" in raised.value.public_message

    def test_unknown_db_backend_names_the_offending_variable(
        self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path
    ) -> None:
        monkeypatch.setenv("DB_BACKEND", "postgres")
        with pytest.raises(ConfigurationError) as raised:
            load_environment(absent_dotenv)
        assert raised.value.details["path"] == "DB_BACKEND"


class TestAllowedOrigins:
    """`ALLOWED_ORIGINS` is the CORS allowlist — a comma-separated list, never a wildcard."""

    def test_comma_separated_values_become_a_list(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.test,https://b.test")
        assert load_environment(absent_dotenv).allowed_origins == ("https://a.test", "https://b.test")

    def test_whitespace_and_empty_entries_are_dropped(
        self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path
    ) -> None:
        monkeypatch.setenv("ALLOWED_ORIGINS", " https://a.test , , https://b.test ,")
        assert load_environment(absent_dotenv).allowed_origins == ("https://a.test", "https://b.test")

    def test_a_single_origin_still_becomes_a_list(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("ALLOWED_ORIGINS", "https://a.test")
        assert load_environment(absent_dotenv).allowed_origins == ("https://a.test",)

    def test_empty_value_disables_cors(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("ALLOWED_ORIGINS", "")
        assert load_environment(absent_dotenv).allowed_origins == ()

    @pytest.mark.parametrize(
        "raw",
        [
            "*",  # the classic wildcard, catastrophic with credentialed CORS
            "https://a.test,*",  # a wildcard hiding behind a valid entry
            "https://*.a.test",  # subdomain wildcard
            "https://a.com/path",  # an origin has no path
            "https://a.com/",  # not even a bare slash
            "https://a.com?q=1",  # or a query
            "https://a.com#frag",  # or a fragment
            "ftp://a.com",  # non-http(s) scheme
            "a.com",  # no scheme at all
            "https://",  # no host
            "https://user:pw@a.com",  # userinfo is not part of an origin
        ],
    )
    def test_non_origin_entries_are_rejected_at_load(
        self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path, raw: str
    ) -> None:
        monkeypatch.setenv("ALLOWED_ORIGINS", raw)
        with pytest.raises(ConfigurationError) as raised:
            load_environment(absent_dotenv)
        assert raised.value.details["path"] == "ALLOWED_ORIGINS"
        assert "ALLOWED_ORIGINS" in raised.value.public_message

    def test_explicit_ports_are_accepted(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("ALLOWED_ORIGINS", "http://localhost:3000,https://a.test:8443")
        assert load_environment(absent_dotenv).allowed_origins == ("http://localhost:3000", "https://a.test:8443")


class TestCookieSecure:
    """The tri-state flag and the effective value the app actually uses."""

    @pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", "on"])
    def test_truthy_values(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path, raw: str) -> None:
        monkeypatch.setenv("COOKIE_SECURE", raw)
        assert load_environment(absent_dotenv).cookie_secure is True

    @pytest.mark.parametrize("raw", ["0", "false", "FALSE", "no", "off"])
    def test_falsy_values(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path, raw: str) -> None:
        monkeypatch.setenv("COOKIE_SECURE", raw)
        assert load_environment(absent_dotenv).cookie_secure is False

    def test_empty_value_means_unset(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("COOKIE_SECURE", "  ")
        assert load_environment(absent_dotenv).cookie_secure is None

    def test_garbage_raises_a_configuration_error_with_the_path(
        self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path
    ) -> None:
        monkeypatch.setenv("COOKIE_SECURE", "maybe")
        with pytest.raises(ConfigurationError) as raised:
            load_environment(absent_dotenv)
        assert raised.value.details["path"] == "COOKIE_SECURE"
        assert "COOKIE_SECURE" in raised.value.public_message

    @pytest.mark.parametrize(
        ("app_env", "cookie_secure", "expected"),
        [
            ("dev", None, False),
            ("staging", None, False),
            ("prod", None, True),
            ("dev", True, True),
            ("prod", False, False),
        ],
    )
    def test_effective_value(
        self, app_env: Literal["dev", "staging", "prod"], cookie_secure: bool | None, expected: bool
    ) -> None:
        env = Environment(app_env=app_env, cookie_secure=cookie_secure)
        assert env.cookie_secure_effective is expected


class TestDotenv:
    """The env file is a convenience for developers; real variables always win."""

    def test_values_are_read_from_the_file(self, tmp_path: Path) -> None:
        dotenv = tmp_path / ".env"
        dotenv.write_text("APP_ENV=staging\nDB_NAME=from_file\n", encoding="utf-8")
        env = load_environment(dotenv)
        assert (env.app_env, env.db_name) == ("staging", "from_file")

    def test_the_default_file_is_read_only_once_per_process(self, monkeypatch: pytest.MonkeyPatch) -> None:
        calls: list[bool] = []
        monkeypatch.setattr(
            "voiceai.core.environment.load_dotenv",
            lambda *_args, **kwargs: calls.append(kwargs.get("override", False)),
        )
        load_environment()
        load_environment()
        assert calls == [False]  # loaded once, and never overriding a real variable

    def test_process_environment_overrides_the_file(self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
        dotenv = tmp_path / ".env"
        dotenv.write_text("APP_ENV=staging\n", encoding="utf-8")
        monkeypatch.setenv("APP_ENV", "prod")
        assert load_environment(dotenv).app_env == "prod"


class TestGetEnvironment:
    """The cached accessor and the reset hook tests rely on."""

    def test_the_environment_is_cached(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "staging")
        first = get_environment()
        assert get_environment() is first
        assert first.app_env == "staging"

    def test_reset_makes_the_next_call_reload(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("APP_ENV", "staging")
        first = get_environment()
        reset_environment()
        monkeypatch.setenv("APP_ENV", "prod")
        second = get_environment()
        assert second is not first
        assert second.app_env == "prod"


class TestAllowedOriginsModelBoundary:
    """The allowlist is validated on the model itself, not only on the env-loader path."""

    def test_direct_construction_with_wildcard_raises(self) -> None:
        with pytest.raises(ConfigurationError) as excinfo:
            Environment(allowed_origins=("*",))
        assert excinfo.value.details["path"] == "ALLOWED_ORIGINS"

    def test_direct_construction_with_path_entry_raises(self) -> None:
        with pytest.raises(ConfigurationError):
            Environment(allowed_origins=("https://a.test/path",))

    def test_the_allowlist_is_an_immutable_tuple(self) -> None:
        env = Environment(allowed_origins=("https://a.test",))
        assert env.allowed_origins == ("https://a.test",)
        assert not hasattr(env.allowed_origins, "append")
