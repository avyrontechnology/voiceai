"""Tenant middleware: credential → context binding behind the real app (spec 0020, M1b)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any
from types import SimpleNamespace
from typing import cast

import pytest
from dependency_injector import providers
from fastapi import FastAPI, Request
from httpx import AsyncClient

from voiceai.common.constants import SESSION_COOKIE
from voiceai.common.errors import DatabaseError
from voiceai.common.tenancy import SYSTEM_TENANT_ID, TenantContext, current_tenant
from voiceai.core.app_factory import create_app
from voiceai.core.environment import Environment
from voiceai.modules.identity import Principal, request_principal

TENANT_PROBE_PATH = "/__tenant__"
ACME_TENANT = "acme"
ACME_SCOPES = frozenset({"calls:read"})
SESSION_TOKEN = "session-token-1"
AUTHORIZATION = "Bearer api-key-1"
REQUEST_ID = "probe-request-id"


async def _probe(request: Request) -> dict[str, Any]:
    """Echo the ambient context plus the stashed principal (test-only route)."""
    context = current_tenant()
    stashed = request_principal(request)
    return {
        "tenant_id": context.tenant_id,
        "request_id": context.request_id,
        "principal_id": context.principal_id,
        "scopes": sorted(context.scopes),
        "stashed_user_id": stashed.user_id if stashed is not None else None,
    }


@pytest.fixture
def tenant_app(arch_environment: Environment) -> FastAPI:
    """The project app with no modules and a probe route reading the context."""
    app = create_app(env=arch_environment, modules=[])

    @app.get(TENANT_PROBE_PATH)
    async def probe(request: Request) -> dict[str, Any]:
        return await _probe(request)

    return app


def _override_resolver(app: FastAPI, resolver: Callable[..., Any]) -> None:
    """Point the app container's tenant resolver at a test double."""
    container = app.state.container
    container.tenant_resolver.override(providers.Factory(lambda: resolver))


def _acme_pair(request_id: str) -> tuple[TenantContext, Principal]:
    """A resolved (context, principal) pair for the acme tenant."""
    return (
        TenantContext(
            tenant_id=ACME_TENANT,
            request_id=request_id,
            principal_id="u-1",
            scopes=ACME_SCOPES,
        ),
        Principal(user_id="u-1", email="u@example.com", org_id=ACME_TENANT, role="owner"),
    )


async def test_anonymous_request_binds_the_system_tenant(
    tenant_app: FastAPI, client_factory: Callable[..., AsyncClient]
) -> None:
    """No credential binds system with empty scopes — total, never unbound."""

    async def _anonymous(*args: Any) -> tuple[None, None]:
        return None, None

    _override_resolver(tenant_app, _anonymous)
    async with client_factory(tenant_app) as client:
        response = await client.get(TENANT_PROBE_PATH, headers={"X-Request-ID": REQUEST_ID})

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": SYSTEM_TENANT_ID,
        "request_id": REQUEST_ID,
        "principal_id": None,
        "scopes": [],
        "stashed_user_id": None,
    }


async def test_authenticated_request_binds_the_resolved_tenant(
    tenant_app: FastAPI, client_factory: Callable[..., AsyncClient]
) -> None:
    """Cookie + bearer reach the resolver once; context binds, principal stashes."""
    seen: dict[str, Any] = {}

    async def _resolve(
        session_token: str | None, authorization: str | None, request_id: str
    ) -> tuple[TenantContext, Principal]:
        seen["session_token"] = session_token
        seen["authorization"] = authorization
        seen["request_id"] = request_id
        return _acme_pair(request_id)

    _override_resolver(tenant_app, _resolve)
    async with client_factory(tenant_app) as client:
        response = await client.get(
            TENANT_PROBE_PATH,
            headers={"X-Request-ID": REQUEST_ID, "authorization": AUTHORIZATION},
            cookies={SESSION_COOKIE: SESSION_TOKEN},
        )

    assert response.status_code == 200
    assert response.json() == {
        "tenant_id": ACME_TENANT,
        "request_id": REQUEST_ID,
        "principal_id": "u-1",
        "scopes": ["calls:read"],
        "stashed_user_id": "u-1",
    }
    assert seen == {"session_token": SESSION_TOKEN, "authorization": AUTHORIZATION, "request_id": REQUEST_ID}


async def test_backend_failure_is_a_500_not_silent_anonymous(
    tenant_app: FastAPI, client_factory: Callable[..., AsyncClient]
) -> None:
    """A broken store fails closed: outage never demotes traffic to system."""
    maliciously_broken = True

    async def _resolve(*args: Any) -> tuple[TenantContext | None, Principal | None]:
        if maliciously_broken:
            raise DatabaseError("store is down")
        return None, None

    _override_resolver(tenant_app, _resolve)
    async with client_factory(tenant_app, raise_app_exceptions=False) as client:
        response = await client.get(TENANT_PROBE_PATH)

    assert response.status_code == 500
    assert response.json()["ok"] is False


async def test_missing_resolver_is_a_wiring_error(
    arch_environment: Environment, client_factory: Callable[..., AsyncClient]
) -> None:
    """An app built without tenant resolution fails loudly, not unscoped."""
    from voiceai.core.container import VoiceAIContainer

    bare = cast("VoiceAIContainer", SimpleNamespace())
    app = create_app(env=arch_environment, container=bare, modules=[])

    @app.get(TENANT_PROBE_PATH)
    async def probe(request: Request) -> dict[str, Any]:
        return await _probe(request)

    async with client_factory(app, raise_app_exceptions=False) as client:
        response = await client.get(TENANT_PROBE_PATH)

    assert response.status_code == 500
    assert response.json()["ok"] is False
