"""OAuth2 login connector over fakes (spec 0061).

Offline only: SSRF guard patched per-test (literal IPs need no DNS anyway),
the token endpoint rides `httpx.MockTransport`, Redis is an in-memory dict,
and the vault seals with a test master key. Pinned: PKCE-S256 authorize URL
+ stored state, single-use callback, exchange-failure stores nothing,
refresh reseals + preserves, cross-tenant 404s, and the 401/403 gates.
"""

from __future__ import annotations

import json

import httpx
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
from voiceai.modules import tools as tools_module
from voiceai.modules.auth import InvalidCredentialsError, Principal
from voiceai.modules.tools.errors import InvalidToolError, ToolNotFoundError
from voiceai.modules.tools.models import IntegrationTemplate, PartnerCredential, PartnerIntegration
from voiceai.modules.tools.oauth_service import OAuthService
from voiceai.modules.tools.repository import VaultRepository
from voiceai.modules.tools.vault import CredentialVault

BASE = "http://oauth.test"
PREFIX = "/api/v1"
TENANT = "acme"


class _Redis:
    """In-memory TTL-state double (no expiry enforcement — tests are instant)."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    async def get(self, key: str) -> str | None:
        return self.values.get(key)

    async def setex(self, key: str, ttl: int, value: str) -> None:
        self.values[key] = value

    async def delete(self, key: str) -> None:
        self.values.pop(key, None)


class _Auth:
    """AuthService double scripting one principal for both gate seams."""

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


def _principal(role: str = "owner") -> Principal:
    """Owner principal (carries `platform:write` for the mutating routes)."""
    return Principal(user_id="u-1", email="u@example.com", org_id=TENANT, tenant_id=TENANT, role=role)


def _repos(db: InMemoryDatabase, tenant: str) -> VaultRepository:
    """Vault views bound to one tenant."""
    return VaultRepository(
        credentials=TenantScopedRepository(
            InMemoryRepository[PartnerCredential](db, Collections.PARTNER_CREDENTIALS, PartnerCredential),
            tenant,
            Collections.PARTNER_CREDENTIALS,
        ),
        integrations=TenantScopedRepository(
            InMemoryRepository[PartnerIntegration](db, Collections.PARTNER_INTEGRATIONS, PartnerIntegration),
            tenant,
            Collections.PARTNER_INTEGRATIONS,
        ),
        templates=TenantScopedRepository(
            InMemoryRepository[IntegrationTemplate](db, Collections.INTEGRATION_TEMPLATES, IntegrationTemplate),
            tenant,
            Collections.INTEGRATION_TEMPLATES,
        ),
    )


def _token_handler(tokens: dict | None = None, status: int = 200):
    """MockTransport handler answering the token endpoint."""

    def handler(request: httpx.Request) -> httpx.Response:
        if tokens is None:
            return httpx.Response(status, text="nope")
        return httpx.Response(status, json=tokens)

    return handler


def _service(
    db: InMemoryDatabase,
    redis: _Redis | None = None,
    handler=None,
    base_url: str = "https://api.test",
) -> tuple[OAuthService, _Redis]:
    """OAuth service over memory with an injectable token endpoint."""
    store = redis if redis is not None else _Redis()
    transport = httpx.MockTransport(handler or _token_handler({"access_token": "a1", "refresh_token": "r1"}))
    service = OAuthService(
        repos_for=lambda tenant: _repos(db, tenant),
        vault=CredentialVault("test-master-key"),
        redis=store,
        public_base_url=base_url,
        http_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    return service, store


async def test_authorize_builds_pkce_url_and_stores_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Login URL carries S256 PKCE + state; the record stays server-side."""
    monkeypatch.setattr("voiceai.common.security.is_safe_outbound_url", lambda url, **_: True)
    service, store = _service(InMemoryDatabase())
    result = await service.build_authorize_url(
        tenant_id=TENANT,
        provider="github",
        authorization_endpoint="https://issuer.test/auth",
        token_endpoint="https://issuer.test/token",
        client_id="cid-1",
        scope="repo",
    )
    url = result["authorization_url"]
    assert "response_type=code" in url and "code_challenge_method=S256" in url
    assert "client_id=cid-1" in url and "state=" in url
    assert result["expires_in"] == 600
    assert len(store.values) == 1
    record = json.loads(next(iter(store.values.values())))
    assert record["tenant_id"] == TENANT and record["provider"] == "github"
    assert "verifier" in record and "client_secret" in record


async def test_authorize_dark_without_redis_or_base_url() -> None:
    """Login stays dark without state backend or a callback origin (400)."""
    db = InMemoryDatabase()
    transport = httpx.MockTransport(_token_handler({}))
    dark_redis, _ = _service(db)
    dark_redis_noop = OAuthService(
        repos_for=lambda tenant: _repos(db, tenant),
        vault=CredentialVault("test-master-key"),
        redis=None,
        public_base_url="https://api.test",
        http_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    with pytest.raises(InvalidToolError):
        await dark_redis_noop.build_authorize_url(
            tenant_id=TENANT,
            provider="p",
            authorization_endpoint="https://i.test/a",
            token_endpoint="https://i.test/t",
            client_id="c",
        )
    dark_base, _ = _service(db, base_url="")
    with pytest.raises(InvalidToolError):
        await dark_base.build_authorize_url(
            tenant_id=TENANT,
            provider="p",
            authorization_endpoint="https://i.test/a",
            token_endpoint="https://i.test/t",
            client_id="c",
        )


async def test_authorize_rejects_unsafe_endpoints(monkeypatch: pytest.MonkeyPatch) -> None:
    """SSRF-unsafe endpoints fail before any state is stored (400)."""
    monkeypatch.setattr("voiceai.common.security.is_safe_outbound_url", lambda url, **_: False)
    service, store = _service(InMemoryDatabase())
    with pytest.raises(InvalidToolError):
        await service.build_authorize_url(
            tenant_id=TENANT,
            provider="p",
            authorization_endpoint="http://169.254.169.254/a",
            token_endpoint="https://i.test/t",
            client_id="c",
        )
    assert store.values == {}


async def _seed_dcr_row(db: InMemoryDatabase) -> None:
    """Store a DCR-issued client row as Slice 1 leaves it (secret sealed,
    endpoints in auth_config, no access token)."""
    import json as _json

    vault = CredentialVault("test-master-key")
    envelope = vault.encrypt(_json.dumps({"client_secret": "dcr-shh"}).encode("utf-8"), key_version=1)
    row = PartnerCredential(
        provider="github",
        credential_type="oauth2",
        auth_config={
            "client_id": "dcr-cid",
            "issuer_url": "https://issuer.test",
            "authorization_endpoint": "https://issuer.test/auth",
            "token_endpoint": "https://issuer.test/token",
        },
        encrypted_data=envelope,
        key_version=1,
    )
    await _repos(db, TENANT).save_credential(row)


async def test_authorize_resolves_dcr_client_from_vault(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider-only login resolves endpoints + credentials server-side —
    the secret rides the state record, never the URL or the caller."""
    monkeypatch.setattr("voiceai.common.security.is_safe_outbound_url", lambda url, **_: True)
    db = InMemoryDatabase()
    await _seed_dcr_row(db)
    service, store = _service(db)
    result = await service.build_authorize_url(tenant_id=TENANT, provider="github", scope="repo")
    url = result["authorization_url"]
    assert url.startswith("https://issuer.test/auth?")
    assert "client_id=dcr-cid" in url
    assert "dcr-shh" not in url
    record = json.loads(next(iter(store.values.values())))
    assert record["client_id"] == "dcr-cid" and record["client_secret"] == "dcr-shh"
    assert record["token_endpoint"] == "https://issuer.test/token"


async def test_authorize_without_dcr_record_is_400(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provider-only login with no DCR record fails closed (400)."""
    monkeypatch.setattr("voiceai.common.security.is_safe_outbound_url", lambda url, **_: True)
    service, store = _service(InMemoryDatabase())
    with pytest.raises(InvalidToolError):
        await service.build_authorize_url(tenant_id=TENANT, provider="github", scope="repo")
    assert store.values == {}


async def test_callback_happy_path_seals_and_consumes_state(monkeypatch: pytest.MonkeyPatch) -> None:
    """Code exchange seals tokens; the state is single-use (replay 400s)."""
    monkeypatch.setattr("voiceai.common.security.is_safe_outbound_url", lambda url, **_: True)
    db = InMemoryDatabase()
    service, store = _service(db, handler=_token_handler({"access_token": "a1", "refresh_token": "r1"}))
    await service.build_authorize_url(
        tenant_id=TENANT,
        provider="github",
        authorization_endpoint="https://i.test/a",
        token_endpoint="https://i.test/t",
        client_id="cid-1",
        client_secret="shh",
    )
    state = next(iter(store.values.keys())).split("oauth:state:")[1]
    assert await service.handle_callback(code="code-1", state=state) == "github"
    assert store.values == {}
    rows = await _repos(db, TENANT).find_credentials_by_provider("github")
    assert len(rows) == 1 and rows[0].credential_type == "oauth2"
    assert rows[0].auth_config["client_id"] == "cid-1"
    with pytest.raises(InvalidToolError):
        await service.handle_callback(code="code-1", state=state)


async def test_callback_unknown_state_and_failed_exchange_store_nothing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad states and refused grants fail closed with nothing persisted."""
    monkeypatch.setattr("voiceai.common.security.is_safe_outbound_url", lambda url, **_: True)
    db = InMemoryDatabase()
    refused, _ = _service(db, handler=_token_handler(None, status=400))
    with pytest.raises(InvalidToolError):
        await refused.handle_callback(code="c", state="nope")
    service, store = _service(db, handler=_token_handler(None, status=400))
    await service.build_authorize_url(
        tenant_id=TENANT,
        provider="github",
        authorization_endpoint="https://i.test/a",
        token_endpoint="https://i.test/t",
        client_id="cid-1",
    )
    from voiceai.modules.tools.oauth_service import _STATE_KEY_PREFIX as _prefix

    state = next(iter(store.values.keys())).split(_prefix)[1]
    with pytest.raises(InvalidToolError):
        await service.handle_callback(code="c", state=state)
    assert await _repos(db, TENANT).find_credentials_by_provider("github") == []


async def test_refresh_rotates_and_preserves() -> None:
    """Refresh reseals the new access token and keeps secret + refresh fallback."""

    def rotate(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"access_token": "a2"})

    db = InMemoryDatabase()
    service, _ = _service(db, handler=rotate)
    vault = CredentialVault("test-master-key")
    material = {"token": "a1", "refresh_token": "r1", "client_secret": "shh"}
    row = PartnerCredential(
        provider="github",
        credential_type="oauth2",
        auth_config={"client_id": "cid-1", "token_endpoint": "https://i.test/t"},
        encrypted_data=vault.encrypt(json.dumps(material).encode("utf-8"), key_version=1),
        key_version=1,
    )
    saved = await _repos(db, TENANT).save_credential(row)
    assert saved.id is not None
    resealed = await service.refresh_credential(tenant_id=TENANT, credential_id=saved.id)
    opened = json.loads(vault.decrypt(resealed.encrypted_data, key_version=1).decode("utf-8"))
    assert opened == {"token": "a2", "refresh_token": "r1", "client_secret": "shh"}


async def test_refresh_refuses_without_refresh_token_and_missing() -> None:
    """Rows without refresh material (or foreign rows) fail loudly."""
    db = InMemoryDatabase()
    service, _ = _service(db)
    vault = CredentialVault("test-master-key")
    row = PartnerCredential(
        provider="github",
        credential_type="oauth2",
        auth_config={"client_id": "cid-1", "token_endpoint": "https://i.test/t"},
        encrypted_data=vault.encrypt(json.dumps({"token": "a1"}).encode("utf-8"), key_version=1),
        key_version=1,
    )
    saved = await _repos(db, TENANT).save_credential(row)
    assert saved.id is not None
    with pytest.raises(InvalidToolError):
        await service.refresh_credential(tenant_id=TENANT, credential_id=saved.id)
    with pytest.raises(ToolNotFoundError):
        await service.refresh_credential(tenant_id=TENANT, credential_id="missing")
    key_row = PartnerCredential(
        provider="plain",
        credential_type="api_key",
        auth_config={},
        encrypted_data=vault.encrypt(json.dumps({"api_key": "k"}).encode("utf-8"), key_version=1),
        key_version=1,
    )
    saved_key = await _repos(db, TENANT).save_credential(key_row)
    assert saved_key.id is not None
    with pytest.raises(InvalidToolError):
        await service.refresh_credential(tenant_id=TENANT, credential_id=saved_key.id)


async def test_callback_page_escapes_provider() -> None:
    """Popup pages escape caller input (no HTML injection via provider)."""
    service, _ = _service(InMemoryDatabase())
    html, status = service.callback_page(ok=True, provider='"><script>alert(1)</script>', detail="Connected.")
    assert status == 200
    assert "<script>alert(1)</script>" not in html
    assert "oauth:connected" in html
    _, bad_status = service.callback_page(ok=False, provider="", detail="Nope.")
    assert bad_status == 400


async def _client(role: str | None = "owner", base_url: str = "https://api.test") -> AsyncClient:
    """Factory app serving the OAuth service over memory + fake Redis."""
    db = InMemoryDatabase()
    store = _Redis()
    transport = httpx.MockTransport(_token_handler({"access_token": "a1", "refresh_token": "r1"}))
    from voiceai.modules.tools.oauth_service import OAuthService as _OAuth

    service = _OAuth(
        repos_for=lambda tenant: _repos(db, tenant),
        vault=CredentialVault("test-master-key"),
        redis=store,
        public_base_url=base_url,
        http_factory=lambda: httpx.AsyncClient(transport=transport),
    )
    container = build_container(Environment())
    container.oauth_service.override(providers.Object(service))
    container.auth_service.override(providers.Object(_Auth(None if role is None else _principal(role))))
    app = create_app(env=Environment(), container=container, modules=[tools_module.MODULE])
    return AsyncClient(transport=ASGITransport(app=app), base_url=BASE)


async def test_controller_authorize_and_callback_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """End-to-end: authorize URL → browser callback stores the credential."""
    monkeypatch.setattr(
        "voiceai.common.security.is_safe_outbound_url",
        lambda url, **_: url.startswith("https://issuer.test"),
    )
    async with await _client() as client:
        authorize = await client.post(
            f"{PREFIX}/oauth/authorize-url",
            json={
                "provider": "github",
                "authorization_endpoint": "https://issuer.test/auth",
                "token_endpoint": "https://issuer.test/token",
                "client_id": "cid-1",
            },
        )
        assert authorize.status_code == 200
        assert authorize.json()["data"]["authorization_url"].startswith("https://issuer.test/auth?")

        bad = await client.post(
            f"{PREFIX}/oauth/authorize-url",
            json={
                "provider": "github",
                "authorization_endpoint": "http://169.254.169.254/auth",
                "token_endpoint": "https://issuer.test/token",
                "client_id": "cid-1",
            },
        )
        assert bad.status_code == 400


async def test_controller_callback_pages_and_refresh_404() -> None:
    """Unknown states render the error page; missing rows 404."""
    async with await _client() as client:
        page = await client.get(f"{PREFIX}/oauth/callback", params={"code": "c", "state": "nope"})
        assert page.status_code == 400
        assert "oauth:connected" in page.text
        refresh = await client.post(f"{PREFIX}/partner-credentials/missing/refresh")
        assert refresh.status_code == 404


async def test_controller_anonymous_is_401() -> None:
    """Anonymous callers cannot mint logins or refresh (gate first)."""
    async with await _client(role=None) as client:
        assert (
            await client.post(
                f"{PREFIX}/oauth/authorize-url",
                json={
                    "provider": "github",
                    "authorization_endpoint": "https://issuer.test/auth",
                    "token_endpoint": "https://issuer.test/token",
                    "client_id": "cid-1",
                },
            )
        ).status_code == 401
