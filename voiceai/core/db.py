"""Database client factory (AGENTS.md rule 4).

Spec 0001 ships the interface and an in-memory implementation; spec 0003 adds the real
``mongo`` backend over motor (async-native, so no thread offload on the event loop).
Everything above this file talks to `DatabaseClient`, so backends land here without
touching a repository.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final, Protocol

from voiceai.common.errors import ConfigurationError
from voiceai.core.environment import DB_BACKEND_MEMORY, DB_BACKEND_MONGO, Environment
from voiceai.database.constants import (
    MONGO_CONNECT_TIMEOUT_MS,
    MONGO_SERVER_SELECTION_TIMEOUT_MS,
    MONGO_SOCKET_TIMEOUT_MS,
    MONGO_TIMEOUT_MS,
)

__all__ = ["DatabaseClient", "InMemoryDatabase", "MotorDatabase", "create_db"]

_ENV_PATH_DB_BACKEND: Final[str] = "DB_BACKEND"
_DETAIL_KEY_BACKEND: Final[str] = "backend"
_MONGO_DRIVER_MESSAGE: Final[str] = "mongo driver not installed; pip install motor (spec 0003)"
_MONGO_URL_MESSAGE: Final[str] = "mongo backend selected but no DB_URL is configured"
_MONGO_BACKEND_MESSAGE: Final[str] = "unknown database backend selected"


class DatabaseClient(Protocol):
    """What the rest of the system may assume about a database client.

    Deliberately tiny: repositories depend on the concrete backend they were built for, while
    everything else (health probes, logs) only needs to name the backend in use.
    """

    name: str


class InMemoryDatabase:
    """Process-local database used by tests and by the default `memory` backend.

    Attributes:
        name: Backend name reported to health probes and logs.
        collections: `{collection_name: {item_id: document}}`. Repositories own the document
            shape; this class is only the storage.
    """

    name: str = DB_BACKEND_MEMORY

    def __init__(self) -> None:
        self.collections: dict[str, dict[str, dict[str, Any]]] = {}  # why: documents are free-form


def _default_motor_client(db_url: str, **kwargs: Any) -> Any:
    """Build the real motor client (spec 0003).

    Imported lazily so environments without the driver still import this module;
    tests inject a recording fake through ``MotorDatabase``'s factory seam instead.

    Args:
        db_url: The MongoDB connection string (credentials stay in the environment).
        kwargs: Timeout kwargs the caller pins (asserted by the factory-seam tests).

    Returns:
        An unopened ``AsyncIOMotorClient`` (motor connects lazily on first use).

    Raises:
        ConfigurationError: When the ``motor`` package is not installed.
    """
    try:
        from motor.motor_asyncio import AsyncIOMotorClient
    except ImportError as exc:
        raise ConfigurationError(_MONGO_DRIVER_MESSAGE, path="motor") from exc
    return AsyncIOMotorClient(db_url, **kwargs)


class MotorDatabase:
    """MongoDB backend over motor (spec 0003).

    One handle serves every repository: ``db[collection.value]`` selects the
    collection, so repositories never see the client. Timeouts are fixed at
    construction (AGENTS.md §4 — a hung database must degrade, never wedge a loop).

    Attributes:
        name: Backend name reported to health probes and logs.
    """

    name: str = DB_BACKEND_MONGO

    def __init__(
        self,
        db_url: str,
        db_name: str,
        *,
        client_factory: Callable[..., Any] | None = None,  # why: recording-fake seam + lazy motor import
    ) -> None:
        factory = client_factory or _default_motor_client
        self._client = factory(
            db_url,
            serverSelectionTimeoutMS=MONGO_SERVER_SELECTION_TIMEOUT_MS,
            timeoutMS=MONGO_TIMEOUT_MS,
            connectTimeoutMS=MONGO_CONNECT_TIMEOUT_MS,
            socketTimeoutMS=MONGO_SOCKET_TIMEOUT_MS,
        )
        self._database = self._client[db_name]

    def __getitem__(self, collection_name: str) -> Any:
        """Select one collection by name (the repository seam).

        Args:
            collection_name: Collection to operate on.

        Returns:
            The motor collection handle (or the fake's equivalent in tests).
        """
        return self._database[collection_name]

    def close(self) -> None:
        """Shut the client down; safe to call without ever connecting."""
        self._client.close()


def create_db(env: Environment) -> DatabaseClient:
    """Create the database client selected by the environment.

    Args:
        env: The process configuration; `db_backend` selects the implementation.

    Returns:
        A client implementing `DatabaseClient`.

    Raises:
        ConfigurationError: When a backend is selected that this spec does not ship. Failing at
            startup beats discovering a missing driver on the first request.
    """
    if env.db_backend == DB_BACKEND_MEMORY:
        return InMemoryDatabase()
    if env.db_backend != DB_BACKEND_MONGO:
        raise ConfigurationError(
            _MONGO_BACKEND_MESSAGE,
            path=_ENV_PATH_DB_BACKEND,
            details={_DETAIL_KEY_BACKEND: env.db_backend},
        )
    if not env.db_url:
        raise ConfigurationError(
            _MONGO_URL_MESSAGE,
            path=_ENV_PATH_DB_BACKEND,
            details={_DETAIL_KEY_BACKEND: env.db_backend},
        )
    return MotorDatabase(env.db_url, env.db_name)
