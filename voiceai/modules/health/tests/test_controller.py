"""The health endpoints end to end, through the real app factory and ASGI transport."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from typing import Any

import httpx
import pytest
from fastapi import FastAPI

from voiceai.common.constants import (
    API_PREFIX,
    CONTAINER_KEY_REDIS,
    HTTP_OK,
    HTTP_SERVICE_UNAVAILABLE,
    REQUEST_ID_HEADER,
)
from voiceai.common.errors import ErrorCode
from voiceai.modules.health.constants import LIVE_PATH, LIVENESS_FIELD, READY_PATH, ROUTE_PREFIX
from voiceai.modules.health.models import HealthState

HEALTH_URL = f"{API_PREFIX}{ROUTE_PREFIX}"
LIVE_URL = f"{HEALTH_URL}{LIVE_PATH}"
READY_URL = f"{HEALTH_URL}{READY_PATH}"
INBOUND_REQUEST_ID = "req-arch-0001"
DRIVER_SECRET = "connection refused to 10.0.0.1:6379"


class LeakyRedis:
    """Redis double that fails with a known sentinel string.

    The shared `failing_redis` fixture covers the outage; this local double exists so the leak
    assertion below has text it knows must never appear in a response (AGENTS.md §4).
    """

    async def ping(self) -> bool:
        """Fail the ping with driver text that must not reach a client."""
        raise ConnectionError(DRIVER_SECRET)

    async def aclose(self) -> None:
        """Match the shutdown contract the container calls on teardown."""


@pytest.fixture
async def unready_client(
    arch_app: FastAPI,
    container_override: Callable[[FastAPI, str, Any], None],
    client_factory: Callable[..., httpx.AsyncClient],
) -> AsyncIterator[httpx.AsyncClient]:
    """Drive the real app with an unreachable redis behind the container's `redis` key.

    The fake goes in by re-registering that key on the built application — the same seam
    production wiring uses, so no module internal is patched.
    """
    container_override(arch_app, CONTAINER_KEY_REDIS, LeakyRedis())
    async with client_factory(arch_app) as client:
        yield client


async def test_report_returns_the_success_envelope(arch_client: httpx.AsyncClient) -> None:
    """GET on the module root answers directly (no trailing-slash redirect) in the envelope."""
    response = await arch_client.get(HEALTH_URL)
    body = response.json()

    assert response.status_code == HTTP_OK
    assert body["ok"] is True
    assert body["data"]["status"] == HealthState.UP.value
    assert [component["name"] for component in body["data"]["components"]]
    assert body["data"]["version"]


async def test_report_echoes_the_request_id(arch_client: httpx.AsyncClient) -> None:
    """Every response carries the correlation id that stitches it to the logs."""
    response = await arch_client.get(HEALTH_URL, headers={REQUEST_ID_HEADER: INBOUND_REQUEST_ID})

    assert response.headers[REQUEST_ID_HEADER] == INBOUND_REQUEST_ID


async def test_liveness_returns_the_minimal_shape(arch_client: httpx.AsyncClient) -> None:
    """Orchestrators poll this constantly, so the payload stays one field."""
    response = await arch_client.get(LIVE_URL)
    body = response.json()

    assert response.status_code == HTTP_OK
    assert body["data"] == {LIVENESS_FIELD: HealthState.UP.value}
    assert response.headers[REQUEST_ID_HEADER]


async def test_readiness_is_ok_while_dependencies_are_usable(arch_client: httpx.AsyncClient) -> None:
    """With nothing broken, readiness returns the same report as the root endpoint."""
    response = await arch_client.get(READY_URL)

    assert response.status_code == HTTP_OK
    assert response.json()["data"]["status"] == HealthState.UP.value


async def test_readiness_answers_503_when_a_dependency_is_down(unready_client: httpx.AsyncClient) -> None:
    """A dead dependency takes the instance out of rotation through the error envelope."""
    response = await unready_client.get(READY_URL)
    body = response.json()

    assert response.status_code == HTTP_SERVICE_UNAVAILABLE
    assert body["ok"] is False
    assert body["error"]["code"] == ErrorCode.DEPENDENCY_UNAVAILABLE.value
    assert body["error"]["error_id"]
    assert body["error"]["retryable"] is True
    assert DRIVER_SECRET not in response.text
    assert response.headers[REQUEST_ID_HEADER]


async def test_liveness_survives_a_dead_dependency(unready_client: httpx.AsyncClient) -> None:
    """Liveness and readiness answer different questions; only readiness fails here."""
    response = await unready_client.get(LIVE_URL)

    assert response.status_code == HTTP_OK
    assert response.json()["data"] == {LIVENESS_FIELD: HealthState.UP.value}
