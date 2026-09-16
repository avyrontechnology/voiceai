"""The database factory and the in-memory backend spec 0001 ships."""

from __future__ import annotations

import pytest

from voiceai.common.errors import ConfigurationError
from voiceai.core.db import DatabaseClient, InMemoryDatabase, create_db
from voiceai.core.environment import Environment


class TestCreateDb:
    """Backend selection happens once, at startup."""

    def test_memory_backend_is_the_default(self, arch_environment: Environment) -> None:
        client = create_db(arch_environment)
        assert isinstance(client, InMemoryDatabase)
        assert client.name == "memory"

    def test_each_call_returns_an_isolated_database(self, arch_environment: Environment) -> None:
        first = create_db(arch_environment)
        second = create_db(arch_environment)
        assert first is not second

    def test_mongo_fails_at_startup_with_the_spec_reference(self, arch_environment: Environment) -> None:
        env = arch_environment.model_copy(update={"db_backend": "mongo"})
        with pytest.raises(ConfigurationError) as raised:
            create_db(env)
        assert raised.value.details["path"] == "DB_BACKEND"
        assert "spec 0003" in raised.value.public_message


class TestInMemoryDatabase:
    """The storage repositories build on."""

    def test_starts_empty(self) -> None:
        assert InMemoryDatabase().collections == {}

    def test_collections_are_per_instance(self) -> None:
        first = InMemoryDatabase()
        second = InMemoryDatabase()
        first.collections.setdefault("users", {})["1"] = {"name": "a"}
        assert second.collections == {}

    def test_satisfies_the_database_client_protocol(self) -> None:
        # Explicit annotation so mypy checks the structural conformance too (spec 0001).
        client: DatabaseClient = InMemoryDatabase()
        assert client.name == "memory"
