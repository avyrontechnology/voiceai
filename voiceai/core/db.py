"""Database client factory (AGENTS.md rule 4).

Spec 0001 ships the interface and an in-memory implementation only; choosing and wiring a real
driver is spec 0003. Everything above this file talks to `DatabaseClient`, so that spec adds a
backend here without touching a repository.
"""

from __future__ import annotations

from typing import Any, Final, Protocol

from voiceai.common.errors import ConfigurationError
from voiceai.core.environment import DB_BACKEND_MEMORY, DB_BACKEND_MONGO, Environment

__all__ = ["DatabaseClient", "InMemoryDatabase", "create_db"]

_ENV_PATH_DB_BACKEND: Final[str] = "DB_BACKEND"
_DETAIL_KEY_BACKEND: Final[str] = "backend"
_MONGO_UNAVAILABLE_MESSAGE: Final[str] = "mongo driver not installed; see spec 0003"


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
    raise ConfigurationError(
        _MONGO_UNAVAILABLE_MESSAGE,
        path=_ENV_PATH_DB_BACKEND,
        details={_DETAIL_KEY_BACKEND: DB_BACKEND_MONGO},
    )
