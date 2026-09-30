"""The single app serves the platform surface over Atlas-shaped storage (spec 0048, Slice B).

Through the real factory and ASGI transport: owner signup on the greenfield auth
module, then the frozen platform routes read and write the bridge collections under
that owner's tenant, API keys live in the auth store and authenticate Bearer calls,
and no bare (un-prefixed) twin answers.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import cast

import pytest
from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.core.app_factory import create_app
from voiceai.core.container import VoiceAIContainer, build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.tests.conftest import _JWT

BASE = "http://single.test"
API = "/api/v1"
OWNER = {"email": "owner@single.test", "name": "Owner", "password": "owner-pass-1"}


@pytest.fixture
def container(arch_environment: Environment) -> VoiceAIContainer:
    built = build_container(arch_environment)
    # Signup mints a token pair, which needs signing keys the offline env leaves dark.
    built.auth_service.override(providers.Object(AuthService(built.auth_store(), jwt=_JWT)))
    return built


@pytest.fixture
async def owner(container: VoiceAIContainer, arch_environment: Environment) -> AsyncIterator[AsyncClient]:
    app = create_app(env=arch_environment, container=container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        response = await client.post(f"{API}/auth/signup", json=OWNER)
        assert response.status_code == 201, response.text
        yield client


@pytest.fixture
async def anonymous(container: VoiceAIContainer, arch_environment: Environment) -> AsyncIterator[AsyncClient]:
    app = create_app(env=arch_environment, container=container)
    async with AsyncClient(transport=ASGITransport(app=app), base_url=BASE) as client:
        yield client


async def test_platform_routes_require_a_principal(owner: AsyncClient, anonymous: AsyncClient) -> None:
    assert (await anonymous.get(f"{API}/phone-numbers")).status_code == 401
    assert (await owner.get(f"{API}/phone-numbers")).status_code == 200


async def test_bare_paths_are_gone(owner: AsyncClient) -> None:
    assert (await owner.get("/phone-numbers")).status_code == 404
    assert (await owner.get("/organization")).status_code == 404


async def test_numbers_round_trip_lands_in_the_bridge_collection(
    owner: AsyncClient, container: VoiceAIContainer
) -> None:
    created = await owner.post(f"{API}/phone-numbers", json={"number": "+15550001111", "provider": "twilio"})
    assert created.status_code == 201, created.text
    number_id = created.json()["number_id"]

    listed = await owner.get(f"{API}/phone-numbers")
    assert [row["number_id"] for row in listed.json()["numbers"]] == [number_id]

    documents = cast(InMemoryDatabase, container.db_client()).collections["platform_numbers"]
    me = (await owner.get(f"{API}/auth/me")).json()["data"]
    tenant = me["user"].get("tenant_id") or me["user"]["org_id"]
    assert documents[number_id]["tenant_id"] == tenant
    assert documents[number_id]["payload"]["number"] == "+15550001111"

    assert (await owner.delete(f"{API}/phone-numbers/{number_id}")).status_code == 200
    assert (await owner.get(f"{API}/phone-numbers")).json()["numbers"] == []
    assert (await owner.delete(f"{API}/phone-numbers/{number_id}")).status_code == 404


async def test_simulated_call_records_an_execution(owner: AsyncClient) -> None:
    started = await owner.post(
        f"{API}/calls/simulate", json={"agent_id": "agent-1", "to_number": "+15550002222", "delay_scale": 0}
    )
    assert started.status_code == 202, started.text
    stats = await owner.get(f"{API}/executions/stats")
    assert stats.status_code == 200
    assert stats.json()["total"] == 1
    listed = await owner.get(f"{API}/executions")
    assert [row["execution_id"] for row in listed.json()["executions"]] == [started.json()["execution_id"]]


async def test_organization_settings_persist(owner: AsyncClient) -> None:
    updated = await owner.put(f"{API}/organization", json={"name": "Otoba Ltd"})
    assert updated.status_code == 200, updated.text
    assert (await owner.get(f"{API}/organization")).json()["name"] == "Otoba Ltd"


async def test_api_keys_live_in_the_auth_store_and_authenticate_bearer_calls(
    owner: AsyncClient, anonymous: AsyncClient, container: VoiceAIContainer
) -> None:
    await owner.post(f"{API}/phone-numbers", json={"number": "+15550003333"})
    minted = await owner.post(f"{API}/api-keys", json={"name": "ci", "scopes": ["platform:read"]})
    assert minted.status_code == 201, minted.text
    secret, key_id = minted.json()["key"], minted.json()["key_id"]

    assert [key.key_id for key in await container.auth_store().list_api_keys()] == [key_id]
    assert "platform_api_keys" not in cast(InMemoryDatabase, container.db_client()).collections

    bearer = {"authorization": f"Bearer {secret}"}
    seen = await anonymous.get(f"{API}/phone-numbers", headers=bearer)
    assert seen.status_code == 200, seen.text
    assert [row["number"] for row in seen.json()["numbers"]] == ["+15550003333"]  # same tenant as the owner
    assert (
        await anonymous.get(f"{API}/phone-numbers", headers={"authorization": "Bearer sk_live_nope"})
    ).status_code == 401

    assert (await owner.delete(f"{API}/api-keys/{key_id}")).status_code == 200
    assert (await anonymous.get(f"{API}/phone-numbers", headers=bearer)).status_code == 401
