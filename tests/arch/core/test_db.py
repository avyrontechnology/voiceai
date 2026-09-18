"""The database factory and the in-memory backend spec 0001 ships."""

from __future__ import annotations

import sys
from typing import Any
from unittest.mock import MagicMock

import pytest

from voiceai.common.errors import ConfigurationError
from voiceai.core.db import DatabaseClient, InMemoryDatabase, MotorDatabase, create_db
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

    def test_mongo_without_a_url_fails_at_startup(self, arch_environment: Environment) -> None:
        env = arch_environment.model_copy(update={"db_backend": "mongo", "db_url": ""})
        with pytest.raises(ConfigurationError) as raised:
            create_db(env)
        assert raised.value.details["path"] == "DB_BACKEND"

    def test_unknown_backend_fails_at_startup(self, arch_environment: Environment) -> None:
        env = arch_environment.model_copy(update={"db_backend": "mongo"})
        object.__setattr__(env, "db_backend", "postgres")
        with pytest.raises(ConfigurationError) as raised:
            create_db(env)
        assert raised.value.details["path"] == "DB_BACKEND"

    def test_mongo_without_a_driver_names_the_package(
        self, arch_environment: Environment, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        env = arch_environment.model_copy(update={"db_backend": "mongo", "db_url": "mongodb://db:27017"})
        monkeypatch.setitem(sys.modules, "motor.motor_asyncio", None)
        with pytest.raises(ConfigurationError) as raised:
            create_db(env)
        assert "motor" in raised.value.public_message


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


class TestMotorDatabase:
    """The mongo backend: timeouts pinned at construction, closable without connecting."""

    def test_satisfies_the_database_client_protocol(self) -> None:
        factory_calls: list[dict[str, Any]] = []

        def factory(db_url: str, **kwargs: Any) -> MagicMock:
            factory_calls.append({"db_url": db_url, **kwargs})
            client = MagicMock()
            client.__getitem__.return_value = MagicMock()
            return client

        client: DatabaseClient = MotorDatabase("mongodb://db:27017", "otoba_test", client_factory=factory)
        assert client.name == "mongo"
        (call,) = factory_calls
        assert call["db_url"] == "mongodb://db:27017"
        assert call["serverSelectionTimeoutMS"] == 5000
        assert call["timeoutMS"] == 5000
        assert call["connectTimeoutMS"] == 5000
        assert call["socketTimeoutMS"] == 20000

    def test_close_releases_the_client(self) -> None:
        client = MagicMock()
        client.__getitem__.return_value = MagicMock()
        database = MotorDatabase("mongodb://db:27017", "otoba_test", client_factory=lambda *a, **k: client)
        database.close()
        client.close.assert_called_once_with()

    def test_collection_selection_indexes_the_database(self) -> None:
        collections: dict[str, Any] = {}
        database_handle = MagicMock()
        database_handle.__getitem__.side_effect = lambda key: collections.setdefault(key, MagicMock(name=key))

        def factory(db_url: str, **kwargs: Any) -> Any:
            client = MagicMock()
            client.__getitem__.return_value = database_handle
            return client

        database = MotorDatabase("mongodb://db:27017", "otoba_test", client_factory=factory)
        assert database["agents"] is database["agents"]
        assert database["agents"] is not database["users"]
