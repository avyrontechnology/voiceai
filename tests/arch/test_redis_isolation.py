"""Redis isolation: the greenfield stack's Redis footprint is exactly the denylist.

Regression for the Upstash quota incident: sessions, refresh rows and users live
in the database — Redis sees only the revocation fast path (one GET per JWT
auth, one SET per logout) plus one PING per health report. With every Redis URL
empty the stack still serves (probes report SKIPPED, revocation reads fall
through to the store). A counting double backs the providers, so any future
Redis touchpoint without an isolated URL breaks these tests by design.
"""

from __future__ import annotations

import httpx
import pytest
from dependency_injector import providers
from fastapi import FastAPI

from voiceai.common.constants import API_PREFIX
from voiceai.core.environment import Environment
from voiceai.modules.auth.constants import REFRESH_COOKIE


class CountingCache:
    """Revocation-cache/redis double that counts every call (must stay empty here)."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def ping(self) -> bool:
        """Count the probe and answer healthy."""
        self.calls.append("ping")
        return True

    async def get(self, key: str) -> None:
        """Count the read and miss (no data, no Redis)."""
        self.calls.append(f"get:{key}")
        return None

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        """Count the write (must never happen in the isolation suite)."""
        self.calls.append(f"set:{key}")
        return True

    async def aclose(self) -> None:
        """Match the shutdown contract the container calls on teardown."""


@pytest.fixture
def isolated_app(arch_environment: Environment) -> FastAPI:
    """The real application with JWT enabled but every Redis URL empty."""
    from voiceai.core.app_factory import create_app
    from voiceai.core.container import build_container
    from voiceai.modules.auth.tests.conftest import _JWT

    env = arch_environment.model_copy(update={"jwt_private_key": _JWT.private_key, "jwt_public_key": _JWT.public_key})
    assert env.redis_cache_url_effective == ""
    assert env.jwt_enabled is True
    return create_app(env=env, container=build_container(env))


async def _signup_and_login(client: httpx.AsyncClient) -> dict[str, object]:
    """Run signup + login, returning the login `data` payload."""
    signup = await client.post(
        f"{API_PREFIX}/auth/signup",
        json={"email": "owner@x.test", "name": "Owner", "password": "owner-pass-1"},
    )
    assert signup.status_code == 201, signup.text
    login = await client.post(
        f"{API_PREFIX}/auth/login",
        json={"email": "owner@x.test", "password": "owner-pass-1"},
    )
    assert login.status_code == 200, login.text
    data: dict[str, object] = login.json()["data"]
    return data


async def test_full_password_flow_costs_four_cache_touches(isolated_app: FastAPI, client_factory) -> None:
    """The whole flow does three denylist reads (one per JWT auth) and one write (logout).

    Sessions, refresh rows and users live in the database; Redis sees only the
    revocation fast path. Any future Redis touchpoint without an isolated URL
    breaks this exact sequence by design.
    """
    container = isolated_app.state.container
    cache = CountingCache()
    container.redis_client.override(providers.Object(cache))
    container.redis_cache.override(providers.Object(cache))
    async with client_factory(isolated_app) as client:
        data = await _signup_and_login(client)
        access = data["access_token"]
        assert isinstance(access, str) and access.count(".") == 2
        # Drop the legacy cookie up front: every JWT auth below must resolve (and
        # bill) through the denylist path, or the sequence assertion proves nothing.
        # The refresh cookie rides explicitly per request from here on.
        refresh_raw = client.cookies.get(REFRESH_COOKIE)
        assert refresh_raw is not None
        client.cookies.clear()

        me = await client.get(f"{API_PREFIX}/auth/me", headers={"authorization": f"Bearer {access}"})
        assert me.status_code == 200, me.text

        rotated = await client.post(f"{API_PREFIX}/auth/refresh", headers={"cookie": f"{REFRESH_COOKIE}={refresh_raw}"})
        assert rotated.status_code == 200, rotated.text
        # The refresh response re-seeds the jar with a fresh legacy cookie: drop it
        # again, or every later call resolves the legacy session and never bills
        # the denylist — the sequence below would prove nothing.
        client.cookies.clear()

        live = await client.get(
            f"{API_PREFIX}/auth/me",
            headers={"authorization": f"Bearer {rotated.json()['data']['access_token']}"},
        )
        assert live.status_code == 200, live.text
        out = await client.post(
            f"{API_PREFIX}/auth/logout",
            headers={"authorization": f"Bearer {rotated.json()['data']['access_token']}"},
        )
        assert out.status_code == 200, out.text

    reads = [call for call in cache.calls if call.startswith("get:auth:denied:")]
    writes = [call for call in cache.calls if call.startswith("set:auth:denied:")]
    assert cache.calls == reads + writes  # nothing but the denylist fast path
    assert len(reads) == 3 and len(writes) == 1
    assert reads[0] != reads[1]  # two distinct access tokens across rotation
    assert reads[1] == reads[2]  # live-check then logout read the same token


async def test_health_probes_skip_unconfigured_redis(isolated_app: FastAPI, client_factory) -> None:
    """Report and readiness answer without a client to ping (SKIPPED, not DOWN)."""
    async with client_factory(isolated_app) as client:
        report = await client.get(f"{API_PREFIX}/health")
        ready = await client.get(f"{API_PREFIX}/health/ready")

    assert report.status_code == 200
    assert ready.status_code == 200
    components = {c["name"]: c["state"] for c in report.json()["data"]["components"]}
    assert components["redis"] == "skipped"


async def test_unconfigured_stack_serves_without_any_client(isolated_app: FastAPI, client_factory) -> None:
    """No overrides at all: the password flow works with every Redis URL empty."""
    async with client_factory(isolated_app) as client:
        data = await _signup_and_login(client)
        me = await client.get(
            f"{API_PREFIX}/auth/me",
            headers={"authorization": f"Bearer {data['access_token']}"},
        )
        assert me.status_code == 200, me.text


async def test_configured_health_costs_exactly_one_ping(isolated_app: FastAPI, client_factory) -> None:
    """With a client present the report pings once; liveness never touches Redis."""
    container = isolated_app.state.container
    cache = CountingCache()
    container.redis_client.override(providers.Object(cache))
    async with client_factory(isolated_app) as client:
        report = await client.get(f"{API_PREFIX}/health")
        assert report.status_code == 200
        live = await client.get(f"{API_PREFIX}/health/live")
        assert live.status_code == 200

    assert cache.calls == ["ping"]
