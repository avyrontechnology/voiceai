"""Sole MongoDB client factory (Constitution V).

All Mongo clients are constructed here and owned by the application
lifespan in ``core.container``. Module code receives sessions/clients via
DI and never builds its own. ODM mapping uses Beanie v2 (Pydantic-v2
native); raw ``pymongo`` appears only in this factory and migrations.
"""

from __future__ import annotations

from inspect import isawaitable
from typing import Optional, Sequence, Type

from beanie import Document, init_beanie
from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase


def create_mongo_client(url: str) -> AsyncMongoClient:
    """Create (but do not connect) an async MongoDB client.

    Connections are lazy: no I/O happens until the first operation, so
    this is safe to call at startup before Mongo is reachable. Decoded
    datetimes are timezone-aware UTC (``tz_aware=True``) because BSON
    carries no timezone and the audit rule requires aware timestamps.

    Args:
        url: MongoDB connection URL (e.g. from ``environment.get_mongo_url``).

    Returns:
        The unconnected async Mongo client.
    """
    return AsyncMongoClient(url, tz_aware=True)


async def close_mongo_client(client: Optional[AsyncMongoClient]) -> None:
    """Close a client created by :func:`create_mongo_client`.

    Awaits the close when the driver returns an awaitable (fakes/mocks in
    tests); the real ``pymongo`` close is synchronous.

    Args:
        client: The client to close; ``None`` is a no-op (offline/tests).
    """
    if client is not None:
        result = client.close()
        if isawaitable(result):
            await result


async def init_odm(database: AsyncDatabase, document_models: Sequence[Type[Document]]) -> None:
    """Initialize Beanie ODM on ``database`` for ``document_models``.

    Registers models AND creates indexes — requires a reachable MongoDB.
    Use :func:`ensure_indexes` for best-effort index creation at boot.

    Note: model *construction* needs no init (see ``BaseDocument``);
    only persistence operations require initialization.

    Args:
        database: The async database handle (``client[db_name]``).
        document_models: Beanie document classes (all subclass
            ``database.base.BaseDocument``).
    """
    await init_beanie(database=database, document_models=list(document_models))


async def ensure_indexes(database: AsyncDatabase, document_models: Sequence[Type[Document]]) -> None:
    """Create indexes, warning (not raising) when Mongo is unreachable.

    Args:
        database: The async database handle.
        document_models: Beanie document classes.
    """
    import logging

    try:
        await init_beanie(database=database, document_models=list(document_models))
    except Exception as exc:
        logging.getLogger(__name__).warning("index creation skipped (mongo unreachable): %s", exc)
