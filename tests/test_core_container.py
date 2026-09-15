"""Tests for voiceai.core.container (US1, task T010).

Hand-rolled FastAPI-Depends composition root (research R-01): one typed
provide_*() per dependency, lifespan-owned async resources, per-seam
test overrides without rebuilding the app.
"""

from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

from voiceai.core.container import (
    AppContainer,
    lifespan,
    provide_logger,
    provide_mongo,
    provide_redis,
    provide_registry,
)


def _make_container(**overrides: Any) -> AppContainer:
    """Build a container with fakes; no live Redis/Mongo required."""
    params: dict[str, Any] = {"redis_client": None, "mongo_client": None, "store": None}
    params.update(overrides)
    return AppContainer.create(**params)


def test_create_holds_injected_dependencies() -> None:
    """Constructor injection is the only way dependencies enter."""
    sentinel_redis = object()
    container = _make_container(redis_client=sentinel_redis)
    assert container.redis_client is sentinel_redis
    assert container.mongo_client is None
    assert container.logger is not None
    assert container.task_registry is not None


async def test_providers_read_app_state_container() -> None:
    """provide_*() resolve the container stashed on app.state."""
    container = _make_container()
    app = FastAPI()
    app.state.container = container

    @app.get("/probe")
    async def probe(
        logger: Any = Depends(provide_logger),
        redis_client: Any = Depends(provide_redis),
        mongo_client: Any = Depends(provide_mongo),
        registry: Any = Depends(provide_registry),
    ) -> dict[str, bool]:
        return {
            "logger": logger is container.logger,
            "redis": redis_client is container.redis_client,
            "mongo": mongo_client is container.mongo_client,
            "registry": registry is container.task_registry,
        }

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/probe")
    assert response.status_code == 200
    assert response.json() == {"logger": True, "redis": True, "mongo": True, "registry": True}


async def test_dependency_overrides_swap_fakes_without_rebuild() -> None:
    """Tests swap one seam via dependency_overrides; the app is untouched."""
    container = _make_container()
    app = FastAPI()
    app.state.container = container
    fake_redis = object()
    app.dependency_overrides[provide_redis] = lambda: fake_redis

    @app.get("/redis")
    async def read_redis(redis_client: Any = Depends(provide_redis)) -> dict[str, bool]:
        return {"is_fake": redis_client is fake_redis}

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/redis")
    assert response.json() == {"is_fake": True}


def test_providers_fail_closed_without_container() -> None:
    """A provider with no container on app.state raises a clear error."""
    import asyncio

    async def _call() -> None:
        class _State:
            pass

        class _App:
            state = _State()

        class _Request:
            app = _App()

        await provide_redis(_Request())  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="container"):
        asyncio.run(_call())


async def test_lifespan_closes_resources() -> None:
    """lifespan() closes redis/mongo/registry even when some are fakes."""
    redis_fake = AsyncMock()
    mongo_fake = AsyncMock()
    container = _make_container(redis_client=redis_fake, mongo_client=mongo_fake)
    app = FastAPI()
    app.state.container = container
    async with lifespan(app):
        assert app.state.container is container
    redis_fake.aclose.assert_awaited_once()
    mongo_fake.close.assert_called_once()
