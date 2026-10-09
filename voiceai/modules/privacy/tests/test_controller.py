"""Privacy endpoints over the real app factory (spec 0071).

Reads need `platform:read`, writes `platform:write`. The auth service is a
double scripting one principal for the middleware stash and the handler
fallback, so the tests pin the 401/403/404/422 envelopes and the happy paths.
"""

from __future__ import annotations

import pytest
from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.common.tenancy import TenantContext
from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules import privacy as privacy_module
from voiceai.modules.auth import InvalidCredentialsError, Principal
from voiceai.modules.privacy.models import ConsentRecord
from voiceai.modules.privacy.repository import ConsentRepository
from voiceai.modules.privacy.service import PrivacyService

BASE = "http://privacy.test"
PREFIX = "/api/v1"
TENANT = "acme"
GRANT_BODY = {"subject_id": "sub-1", "purpose": "analytics", "policy_version": "v3"}


class _Auth:
    """AuthService double scripting one principal (or anonymous) for the whole request."""

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
    """Session principal the way the resolvers build one."""
    return Principal(user_id="u-1", email="u@example.com", org_id=TENANT, tenant_id=TENANT, role=role)


async def _client(role: str | None = "owner") -> AsyncClient:
    """Factory app serving privacy over a tenant view; `role=None` is anonymous."""
    db = InMemoryDatabase()
    repo = ConsentRepository(
        TenantScopedRepository(
            InMemoryRepository[ConsentRecord](db, Collections.CONSENT_RECORDS, ConsentRecord),
            TENANT,
            Collections.CONSENT_RECORDS,
        )
    )
    container = build_container(Environment())
    container.privacy_service.override(providers.Object(PrivacyService(repo)))
    container.auth_service.override(providers.Object(_Auth(None if role is None else _principal(role))))
    app = create_app(env=Environment(), container=container, modules=[privacy_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE)


async def test_grant_round_trip() -> None:
    """Grant → list → withdraw, then reads exclude the tombstone."""
    client = await _client()

    granted = await client.post(f"{PREFIX}/privacy/consent", json=GRANT_BODY)
    assert granted.status_code == 201, granted.text
    assert granted.json()["data"]["granted"] is True

    listed = await client.get(f"{PREFIX}/privacy/consent/sub-1")
    assert listed.status_code == 200, listed.text
    assert [row["purpose"] for row in listed.json()["data"]] == ["analytics"]

    withdrawn = await client.delete(f"{PREFIX}/privacy/consent/sub-1/analytics")
    assert withdrawn.status_code == 200, withdrawn.text
    assert withdrawn.json()["data"]["granted"] is False

    relisted = await client.get(f"{PREFIX}/privacy/consent/sub-1")
    assert relisted.json()["data"] == []


async def test_anonymous_gets_401() -> None:
    """Anonymous callers answer the 401 envelope on every route."""
    client = await _client(role=None)

    for method, path in (
        ("POST", f"{PREFIX}/privacy/consent"),
        ("GET", f"{PREFIX}/privacy/consent/sub-1"),
        ("DELETE", f"{PREFIX}/privacy/consent/sub-1/analytics"),
    ):
        response = await client.request(method, path, json=GRANT_BODY if method == "POST" else None)
        assert response.status_code == 401, response.text


async def test_viewer_cannot_write() -> None:
    """A `platform:read` principal answers 403 on writes, 200 on reads."""
    client = await _client(role="viewer")

    granted = await client.post(f"{PREFIX}/privacy/consent", json=GRANT_BODY)
    assert granted.status_code == 403, granted.text

    listed = await client.get(f"{PREFIX}/privacy/consent/sub-1")
    assert listed.status_code == 200, listed.text


async def test_unknown_purpose_400() -> None:
    """A purpose outside the vocabulary answers 400, never 422."""
    client = await _client()

    response = await client.post(
        f"{PREFIX}/privacy/consent", json={"subject_id": "sub-1", "purpose": "surveillance", "policy_version": "v3"}
    )

    assert response.status_code == 400, response.text


async def test_withdraw_unknown_404() -> None:
    """Withdrawing a pair with no active row answers 404."""
    client = await _client()

    response = await client.delete(f"{PREFIX}/privacy/consent/nobody/analytics")

    assert response.status_code == 404, response.text


async def test_subject_rights_round_trip() -> None:
    """Access → rectification → erasure over the real app factory."""
    client = await _client()
    await client.post(f"{PREFIX}/privacy/consent", json=GRANT_BODY)

    accessed = await client.get(f"{PREFIX}/privacy/subject/sub-1/data")
    assert accessed.status_code == 200, accessed.text
    assert [row["purpose"] for row in accessed.json()["data"]["consent"]] == ["analytics"]

    rectified = await client.patch(
        f"{PREFIX}/privacy/subject/sub-1", json={"purpose": "analytics", "policy_version": "v9"}
    )
    assert rectified.status_code == 200, rectified.text
    assert rectified.json()["data"]["policy_version"] == "v9"

    erased = await client.delete(f"{PREFIX}/privacy/subject/sub-1")
    assert erased.status_code == 200, erased.text
    assert erased.json()["data"]["erased"] is True
    assert erased.json()["data"]["records"] == 1

    relisted = await client.get(f"{PREFIX}/privacy/consent/sub-1")
    assert relisted.json()["data"] == []
