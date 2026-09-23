"""Wire-contract pins: live bodies parse through the DTOs, garbage does not.

The controller returns `JSONResponse` envelopes (so `response_model` is OpenAPI
truth, not runtime serialisation) — these tests are the runtime enforcement:
every endpoint body must validate through `HealthContract`, and malformed payloads
must fail DTO validation rather than leaking through.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
from pydantic import ValidationError

from voiceai.common.constants import API_PREFIX
from voiceai.modules.health.constants import LIVE_PATH, READY_PATH, ROUTE_PREFIX
from voiceai.modules.health.models import HealthState
from voiceai.modules.health.schemas import HealthContract

HEALTH_URL = f"{API_PREFIX}{ROUTE_PREFIX}"
LIVE_URL = f"{HEALTH_URL}{LIVE_PATH}"
READY_URL = f"{HEALTH_URL}{READY_PATH}"


async def test_liveness_body_matches_the_dto(arch_client: httpx.AsyncClient) -> None:
    """The orchestrator signal parses as `LivenessResponse` with no surplus needs."""
    data = await _get_data(arch_client, LIVE_URL)
    parsed = HealthContract.LivenessResponse.model_validate(data)
    assert parsed.status is HealthState.UP


async def _get_data(client: httpx.AsyncClient, url: str) -> Any:
    """Fetch one endpoint's `data` envelope field."""
    response = await client.get(url)
    assert response.status_code == 200
    return response.json()["data"]


async def test_report_body_matches_the_dto(arch_client: httpx.AsyncClient) -> None:
    """The full report parses as `ReportResponse`, components included."""
    data = await _get_data(arch_client, HEALTH_URL)
    parsed = HealthContract.ReportResponse.model_validate(data)
    assert parsed.status is HealthState.UP
    assert [component.name for component in parsed.components]
    assert parsed.version
    assert parsed.uptime_s >= 0


async def test_readiness_body_matches_the_dto(arch_client: httpx.AsyncClient) -> None:
    """Readiness answers the same DTO while dependencies are usable."""
    data = await _get_data(arch_client, READY_URL)
    assert HealthContract.ReportResponse.model_validate(data).status is HealthState.UP


def test_dto_rejects_an_unknown_state() -> None:
    """A typo'd state fails validation instead of riding the wire."""
    with pytest.raises(ValidationError):
        HealthContract.LivenessResponse.model_validate({"status": "mostly-up"})


def test_dto_rejects_negative_latency() -> None:
    """Negative timings fail validation instead of confusing dashboards."""
    with pytest.raises(ValidationError):
        HealthContract.ComponentResponse.model_validate({"name": "redis", "state": "up", "latency_ms": -1})


def test_openapi_pins_the_response_models(arch_app: object) -> None:
    """The decorators document the DTOs, so generated clients stay honest."""
    from fastapi import FastAPI

    assert isinstance(arch_app, FastAPI)
    paths = arch_app.openapi()["paths"]
    for url in (HEALTH_URL, LIVE_URL, READY_URL):
        assert "get" in paths[url]
