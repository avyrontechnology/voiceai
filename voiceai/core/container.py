"""Dependency-injection composition root (Constitution IV).

Hand-rolled FastAPI-``Depends`` wiring (research R-01): no DI library.
``AppContainer`` holds every shared dependency (logger, Redis/Mongo
clients, store, task registry); exactly one typed ``provide_*()`` function
exists per dependency for route handlers, and non-request code receives
dependencies as constructor arguments. Nothing outside this module (plus
the lifespan below) constructs shared clients.

Tests swap any seam via ``app.dependency_overrides`` without rebuilding
the app.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from inspect import isawaitable
from logging import Logger
from typing import Any, AsyncIterator, Optional

from fastapi import FastAPI, Request
from redis.asyncio import Redis

from voiceai.core import db as db_factory
from voiceai.core import environment
from voiceai.core import redis as redis_factory
from voiceai.core.resilience import TaskRegistry
from voiceai.otobaai_logger import get_logger


@dataclass
class AppContainer:
    """Holds the application's shared dependencies.

    Attributes:
        logger: The single project logger (otobaai-logger).
        redis_client: Shared async Redis client, or ``None`` offline.
        mongo_client: Shared async Mongo client, or ``None`` offline.
        store: Persistence backend (memory/Redis/Mongo repository).
        task_registry: Owner of background tasks for graceful teardown.
    """

    logger: Logger
    redis_client: Optional[Redis] = None
    mongo_client: Optional[Any] = None
    store: Optional[Any] = None
    task_registry: TaskRegistry = field(default_factory=lambda: TaskRegistry(name="app"))

    @classmethod
    def create(
        cls,
        *,
        logger: Optional[Logger] = None,
        redis_client: Optional[Redis] = None,
        mongo_client: Optional[Any] = None,
        store: Optional[Any] = None,
        task_registry: Optional[TaskRegistry] = None,
    ) -> "AppContainer":
        """Build a container from explicit (usually fake/test) dependencies.

        Args:
            logger: Logger to use; defaults to the project logger.
            redis_client: Redis client or ``None`` for offline use.
            mongo_client: Mongo client or ``None`` for offline use.
            store: Persistence backend or ``None``.
            task_registry: Registry or a fresh ``app``-named one.

        Returns:
            The assembled container. Nothing is connected here.
        """
        return cls(
            logger=logger or get_logger("voiceai.container"),
            redis_client=redis_client,
            mongo_client=mongo_client,
            store=store,
            task_registry=task_registry or TaskRegistry(name="app"),
        )

    @classmethod
    def create_from_environment(cls) -> "AppContainer":
        """Build a container with real clients from environment config.

        Clients are created unconnected (lazy I/O); the lifespan below
        owns their teardown.

        Returns:
            The assembled container wired to configured Redis/Mongo.
        """
        return cls.create(
            redis_client=redis_factory.create_redis_client(environment.get_redis_url()),
            mongo_client=db_factory.create_mongo_client(environment.get_mongo_url()),
        )

    async def close(self) -> None:
        """Release owned resources: Redis, Mongo, then background tasks."""
        await redis_factory.close_redis_client(self.redis_client)
        await db_factory.close_mongo_client(self.mongo_client)
        await self.task_registry.cancel_all()


def _container_of(request: Request) -> AppContainer:
    """Fetch the container from app state or fail closed.

    Args:
        request: The incoming request.

    Returns:
        The application container.

    Raises:
        RuntimeError: If no container was installed on ``app.state``.
    """
    container = getattr(getattr(request.app, "state", None), "container", None)
    if not isinstance(container, AppContainer):
        raise RuntimeError("no container (AppContainer) installed on app.state (lifespan not run?)")
    return container


async def provide_logger(request: Request) -> Logger:
    """Provide the project logger for a request.

    Args:
        request: The incoming request.

    Returns:
        The container logger.
    """
    return _container_of(request).logger


async def provide_redis(request: Request) -> Optional[Redis]:
    """Provide the shared Redis client (``None`` when offline).

    Args:
        request: The incoming request.

    Returns:
        The container Redis client.
    """
    return _container_of(request).redis_client


async def provide_mongo(request: Request) -> Optional[Any]:
    """Provide the shared Mongo client (``None`` when offline).

    Args:
        request: The incoming request.

    Returns:
        The container Mongo client.
    """
    return _container_of(request).mongo_client


async def provide_store(request: Request) -> Optional[Any]:
    """Provide the persistence backend (``None`` when unwired).

    Args:
        request: The incoming request.

    Returns:
        The container store.
    """
    return _container_of(request).store


async def provide_registry(request: Request) -> TaskRegistry:
    """Provide the background-task registry.

    Args:
        request: The incoming request.

    Returns:
        The container task registry.
    """
    return _container_of(request).task_registry


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Own shared-client lifecycle for the application.

    Installs an :class:`AppContainer` on ``app.state.container`` (building
    one from the environment when absent), initializes Beanie documents
    (offline-safe lazy client), yields to serving, then closes Redis,
    Mongo, and background tasks in order.

    Args:
        app: The FastAPI application.

    Yields:
        Control to the running server.
    """
    from voiceai.platform.models import ALL_DOCUMENT_MODELS

    container = getattr(getattr(app, "state", None), "container", None)
    if not isinstance(container, AppContainer):
        container = AppContainer.create_from_environment()
        app.state.container = container
    client = db_factory.create_mongo_client(environment.get_mongo_url())
    try:
        await db_factory.ensure_indexes(client[environment.get_mongo_db()], ALL_DOCUMENT_MODELS)
    finally:
        await db_factory.close_mongo_client(client)
    try:
        yield
    finally:
        closing = container.close()
        if isawaitable(closing):
            await closing
