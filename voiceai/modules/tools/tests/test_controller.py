"""Tools endpoints over the real app factory (spec 0029, slice 1; spec 0049 gates).

Every route is scope-gated: reads need `platform:read`, writes `platform:write`.
The auth service is a double scripting one principal (or anonymous) for both
seams the gate touches — the middleware stash and the handler fallback — so the
tests pin the 401 / 403 envelopes, the 422 validation envelope on legacy-shaped
bodies, and the happy paths (shapes, tenant merge, 403/404 contracts) end to end.
"""

from __future__ import annotations

import pytest
from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.common.tenancy import SYSTEM_TENANT_ID, TenantContext
from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules import tools as tools_module
from voiceai.modules.auth import InvalidCredentialsError, Principal
from voiceai.modules.tools.errors import ToolNotFoundError
from voiceai.modules.tools.models import ToolDefinition
from voiceai.modules.tools.repository import ToolsRepository
from voiceai.modules.tools.service import ToolsService

BASE = "http://tools.test"
PREFIX = "/api/v1"
TENANT = "acme"
SYSTEM_TOOL = "internal:hangup"
WEBHOOK_BODY = {"kind": "webhook", "name": "notify", "url": "https://hooks.test/n"}
LEGACY_BODY = {"name": "t", "kind": "datetime"}

#: Every route with a body that would pass validation, so only the gate decides.
ROUTES = (
    ("GET", f"{PREFIX}/tools", None),
    ("GET", f"{PREFIX}/tools/{SYSTEM_TOOL}", None),
    ("POST", f"{PREFIX}/tools", WEBHOOK_BODY),
    ("PUT", f"{PREFIX}/tools/webhook:notify", WEBHOOK_BODY),
    ("DELETE", f"{PREFIX}/tools/webhook:notify", None),
)
WRITE_ROUTES = tuple(route for route in ROUTES if route[0] != "GET")


class _Auth:
    """AuthService double scripting one principal (or anonymous) for the whole request.

    Serves both seams the gate touches: the middleware stash
    (`resolve_request_identity`) and the handler fallback (`authenticate`),
    which mirrors the real service on anonymous input by raising the 401.
    """

    def __init__(self, principal: Principal | None) -> None:
        self._principal = principal

    async def resolve_request_identity(
        self, session_token: str | None, authorization: str | None, request_id: str
    ) -> tuple[TenantContext | None, Principal | None]:
        if self._principal is None:
            return None, None
        context = TenantContext(
            tenant_id=self._principal.tenant_id,
            request_id=request_id,
            principal_id=self._principal.user_id,
            scopes=frozenset(self._principal.effective_scopes()),
        )
        return context, self._principal

    async def authenticate(self, session_token: str | None, authorization: str | None) -> Principal:
        if self._principal is None:
            raise InvalidCredentialsError("Authentication required")
        return self._principal


def _principal(role: str) -> Principal:
    """Session principal the way the resolvers build one (`viewer` grants `platform:read` only)."""
    return Principal(user_id="u-1", email="u@example.com", org_id=TENANT, tenant_id=TENANT, role=role)


async def _client(role: str | None = "owner") -> tuple[AsyncClient, ToolsService]:
    """Factory app serving tools over system + tenant views; `role=None` is anonymous."""
    db = InMemoryDatabase()
    system = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            SYSTEM_TENANT_ID,
            Collections.TOOLS,
        )
    )
    tenant = ToolsRepository(
        TenantScopedRepository(
            InMemoryRepository[ToolDefinition](db, Collections.TOOLS, ToolDefinition),
            TENANT,
            Collections.TOOLS,
        )
    )
    service = ToolsService(system, tenant)
    await service.seed()
    container = build_container(Environment())
    container.tools_service.override(providers.Object(service))
    container.auth_service.override(providers.Object(_Auth(None if role is None else _principal(role))))
    app = create_app(env=Environment(), container=container, modules=[tools_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE), service


async def test_list_merges_system_rows() -> None:
    """Seeded internal tools list for a `platform:read` principal."""
    client, _ = await _client()

    response = await client.get(f"{PREFIX}/tools")

    assert response.status_code == 200
    ids = {row["tool_id"] for row in response.json()["data"]["tools"]}
    assert {"internal:hangup", "internal:transfer_call", "internal:knowledge_search"} <= ids


async def test_crud_round_trip_with_system_protection() -> None:
    """Create → read → update → delete, then system rows reject writes."""
    client, _ = await _client()

    created = await client.post(f"{PREFIX}/tools", json=WEBHOOK_BODY)
    assert created.status_code == 201, created.text
    tool_id = created.json()["data"]["tool_id"]
    assert tool_id == "webhook:notify"

    read = await client.get(f"{PREFIX}/tools/{tool_id}")
    assert read.status_code == 200

    updated = await client.put(f"{PREFIX}/tools/{tool_id}", json={**WEBHOOK_BODY, "description": "v2"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["description"] == "v2"

    missing = await client.get(f"{PREFIX}/tools/nope:nope")
    assert missing.status_code == 404

    forbidden = await client.put(f"{PREFIX}/tools/{SYSTEM_TOOL}", json={"kind": "function", "name": "hangup"})
    assert forbidden.status_code == 403
    # why: the system-row guard, not the scope gate (an owner holds `platform:write`).
    assert forbidden.json()["error"]["details"]["tool_id"] == SYSTEM_TOOL

    deleted = await client.delete(f"{PREFIX}/tools/{tool_id}")
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"state": "deleted"}

    gone = await client.get(f"{PREFIX}/tools/{tool_id}")
    assert gone.status_code == 404


@pytest.mark.parametrize(("method", "path", "body"), ROUTES)
async def test_anonymous_callers_answer_401(method: str, path: str, body: dict[str, object] | None) -> None:
    """No credential: every route answers the 401 envelope and writes never reach the store."""
    client, service = await _client(role=None)

    response = await client.request(method, path, json=body)

    assert response.status_code == 401, response.text
    assert response.json()["ok"] is False
    assert response.json()["error"]["code"] == "unauthorized"
    with pytest.raises(ToolNotFoundError):
        await service.get_tool("webhook:notify")


async def test_viewer_reads_but_writes_answer_403() -> None:
    """`platform:read` only: reads pass, every write answers the 403 envelope untouched."""
    client, service = await _client(role="viewer")

    listed = await client.get(f"{PREFIX}/tools")
    assert listed.status_code == 200
    read = await client.get(f"{PREFIX}/tools/{SYSTEM_TOOL}")
    assert read.status_code == 200

    for method, path, body in WRITE_ROUTES:
        response = await client.request(method, path, json=body)
        assert response.status_code == 403, (method, path, response.text)
        assert response.json()["error"]["code"] == "forbidden"
        assert response.json()["error"]["message"] == "Requires platform:write scope"
    with pytest.raises(ToolNotFoundError):
        await service.get_tool("webhook:notify")


async def test_legacy_shaped_body_answers_422() -> None:
    """An unknown `kind` fails request validation (422 envelope naming the field), never a 500."""
    client, _ = await _client()

    created = await client.post(f"{PREFIX}/tools", json=LEGACY_BODY)
    assert created.status_code == 422, created.text
    error = created.json()["error"]
    assert error["code"] == "invalid_request"
    # why: spec 0052 — per-field records are `{loc, type}` only (CONTRACT.md); the submitted
    # value and pydantic's text (which lists the allowed kinds) never come back.
    assert error["details"]["errors"] == [{"loc": ["body", "kind"], "type": "literal_error"}]
    assert LEGACY_BODY["kind"] not in created.text
    assert "Input should be" not in created.text

    updated = await client.put(f"{PREFIX}/tools/function:t", json=LEGACY_BODY)
    assert updated.status_code == 422, updated.text

    extra = await client.post(f"{PREFIX}/tools", json={**WEBHOOK_BODY, "legacy_field": 1})
    assert extra.status_code == 422, extra.text
    assert extra.json()["error"]["details"]["errors"] == [{"loc": ["body", "legacy_field"], "type": "extra_forbidden"}]


async def test_internal_kind_still_answers_400_from_the_service() -> None:
    """`internal` is schema-valid (422 is for unknown kinds) but code-review-owned: 400 (spec 0029)."""
    client, _ = await _client()

    response = await client.post(f"{PREFIX}/tools", json={"kind": "internal", "name": "evil"})

    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "invalid_request"
