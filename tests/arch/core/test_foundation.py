"""T0 foundation gates: isolated URLs, cookie/JWT validation, registry, entry.

Covers the greenfield deltas only; existing suites pin the legacy behavior these
fields fall back to. Hermetic like `test_environment.py`: every test deletes the
variables it cares about and points the loader at an absent file.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from voiceai.common.errors import ConfigurationError
from voiceai.core.environment import Environment, load_environment
from voiceai.database.constants import Collections

FOUNDATION_VARS = (
    "REDIS_URL",
    "REDIS_CACHE_URL",
    "REDIS_BROKER_URL",
    "REDIS_RESULT_URL",
    "MONGO_URL",
    "COOKIE_SAMESITE",
    "COOKIE_DOMAIN",
    "JWT_ACCESS_TTL_S",
    "JWT_REFRESH_TTL_S",
    "JWT_PRIVATE_KEY",
    "JWT_PUBLIC_KEY",
)


@pytest.fixture(autouse=True)
def clean_foundation_env(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Start every test with none of the T0 variables set."""
    for name in FOUNDATION_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def absent_dotenv(tmp_path: Path) -> Path:
    """An env file that does not exist — the hermetic default."""
    return tmp_path / "absent.env"


class TestIsolatedUrls:
    """Each isolated URL falls back to the legacy knob; production sets all three."""

    def test_cache_broker_result_fall_back_to_redis_url(
        self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path
    ) -> None:
        monkeypatch.setenv("REDIS_URL", "redis://shared:6379/0")
        env = load_environment(absent_dotenv)
        assert env.redis_cache_url_effective == "redis://shared:6379/0"
        assert env.redis_broker_url_effective == "redis://shared:6379/0"
        assert env.redis_result_url_effective == "redis://shared:6379/0"

    def test_explicit_isolated_urls_win(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("REDIS_URL", "redis://shared:6379/0")
        monkeypatch.setenv("REDIS_CACHE_URL", "redis://cache:6379/0")
        monkeypatch.setenv("REDIS_BROKER_URL", "redis://broker:6379/0")
        monkeypatch.setenv("REDIS_RESULT_URL", "redis://results:6379/0")
        env = load_environment(absent_dotenv)
        assert env.redis_cache_url_effective == "redis://cache:6379/0"
        assert env.redis_broker_url_effective == "redis://broker:6379/0"
        assert env.redis_result_url_effective == "redis://results:6379/0"

    def test_result_falls_back_to_broker(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("REDIS_BROKER_URL", "redis://broker:6379/0")
        env = load_environment(absent_dotenv)
        assert env.redis_result_url_effective == "redis://broker:6379/0"

    def test_empty_means_disabled(self, absent_dotenv: Path) -> None:
        env = load_environment(absent_dotenv)
        assert env.redis_cache_url_effective == ""
        assert env.redis_broker_url_effective == ""
        assert env.redis_result_url_effective == ""

    def test_mongo_url_wins_over_db_url(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("DB_URL", "mongodb://legacy:27017/otoba")
        monkeypatch.setenv("MONGO_URL", "mongodb+srv://atlas/otoba")
        assert load_environment(absent_dotenv).db_url_effective == "mongodb+srv://atlas/otoba"

    def test_db_url_survives_without_mongo_url(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("DB_URL", "mongodb://legacy:27017/otoba")
        assert load_environment(absent_dotenv).db_url_effective == "mongodb://legacy:27017/otoba"


class TestCookieSamesite:
    """`none` without `Secure` fails closed — the Vercel 401-loop guard."""

    def test_none_with_secure_passes(self) -> None:
        env = Environment(cookie_samesite="none", cookie_secure=True)
        assert env.cookie_samesite == "none"

    def test_none_with_explicit_insecure_fails(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            Environment(cookie_samesite="none", cookie_secure=False)
        assert raised.value.details["path"] == "COOKIE_SAMESITE"

    def test_none_with_prod_default_passes(self) -> None:
        env = Environment(app_env="prod", cookie_samesite="none")
        assert env.cookie_secure_effective is True

    def test_env_value_is_lowercased(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("COOKIE_SAMESITE", "Strict")
        assert load_environment(absent_dotenv).cookie_samesite == "strict"

    def test_garbage_samesite_is_rejected(self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path) -> None:
        monkeypatch.setenv("COOKIE_SAMESITE", "sometimes")
        with pytest.raises(ConfigurationError):
            load_environment(absent_dotenv)


class TestJwtConfig:
    """Half-configured keypairs and inverted TTLs fail closed; empty stays dark."""

    def test_empty_keys_stay_dark(self, absent_dotenv: Path) -> None:
        assert load_environment(absent_dotenv).jwt_enabled is False

    def test_keypair_enables(self) -> None:
        env = Environment(jwt_private_key="pem-private", jwt_public_key="pem-public")
        assert env.jwt_enabled is True

    def test_half_keypair_fails(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            Environment(jwt_private_key="pem-private")
        assert raised.value.details["path"] == "JWT_PRIVATE_KEY"

    def test_escaped_newlines_decode_for_docker_env_files(
        self, monkeypatch: pytest.MonkeyPatch, absent_dotenv: Path
    ) -> None:
        """Literal `\\n` in key vars becomes newlines (Docker env files are single-line)."""
        monkeypatch.setenv("JWT_PRIVATE_KEY", "line-one\\nline-two")
        monkeypatch.setenv("JWT_PUBLIC_KEY", "pub\\nkey")
        env = load_environment(absent_dotenv)
        assert env.jwt_private_key == "line-one\nline-two"
        assert env.jwt_public_key == "pub\nkey"

    def test_non_positive_access_ttl_fails(self) -> None:
        with pytest.raises(ConfigurationError):
            Environment(jwt_private_key="a", jwt_public_key="b", jwt_access_ttl_s=0)

    def test_refresh_must_exceed_access(self) -> None:
        with pytest.raises(ConfigurationError) as raised:
            Environment(
                jwt_private_key="a",
                jwt_public_key="b",
                jwt_access_ttl_s=900,
                jwt_refresh_ttl_s=900,
            )
        assert raised.value.details["path"] == "JWT_REFRESH_TTL_S"


class TestCollectionRegistry:
    """Every greenfield collection is registered exactly once (T2/T3 wire the stores)."""

    def test_auth_and_prompt_collections_exist(self) -> None:
        names = {member.value for member in Collections}
        assert {"sessions", "invites", "auth_events", "api_keys", "revoked_tokens"} <= names
        assert "agent_prompts" in names

    def test_values_stay_unique(self) -> None:
        values = [member.value for member in Collections]
        assert len(values) == len(set(values))


class TestProductionEntry:
    """The thin entry boots the full registry offline, with zero quickstart import."""

    async def test_live_answers_without_touching_quickstart(self, client_factory) -> None:
        import sys

        from voiceai.app import build_app

        app = build_app()
        assert "local_setup.quickstart_server" not in sys.modules
        async with client_factory(app) as client:
            response = await client.get("/api/v1/health/live")
        assert response.status_code == 200
        assert response.json()["data"] == {"status": "up"}
