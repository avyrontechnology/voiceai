"""Credential vault + credential-cache + vault-state behavior (spec 0056, Phase-2).

Offline only: AES-GCM round-trips over fixed master keys, in-memory stores
behind `TenantScopedRepository`, and redis doubles — no sockets, no drivers.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from voiceai.common.errors import ConfigurationError, NotFoundError
from voiceai.common.tenancy import SYSTEM_TENANT_ID
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment, load_environment
from voiceai.core.redis import (
    CREDENTIAL_CACHE_KEY_PREFIX,
    get_cached_credential,
    invalidate_credential_cache,
    set_cached_credential,
)
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.tools.dcr_service import ClientRecord, DcrService
from voiceai.modules.tools.models import IntegrationTemplate, PartnerCredential, PartnerIntegration
from voiceai.modules.tools.repository import VaultRepository
from voiceai.modules.tools.vault import CredentialVault, VaultError

_MASTER_KEY = "phase-two-test-master-key"
_OTHER_KEY = "a-different-test-master-key"
_SECRET = b"oauth-token-abc123"
_CREDENTIAL_ID = "cred-1"


class FakeCredentialCache:
    """In-memory double for the `CredentialCacheClient` protocol."""

    def __init__(self) -> None:
        self._data: dict[str, str] = {}
        self.calls: list[str] = []

    async def get(self, key: str) -> str | None:
        """Record the read and answer from the local map."""
        self.calls.append(f"get:{key}")
        return self._data.get(key)

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        """Record the write and store it (the TTL is accepted, not enforced)."""
        self.calls.append(f"set:{key}")
        self._data[key] = value
        return True

    async def delete(self, key: str) -> int:
        """Record the delete and drop the entry."""
        self.calls.append(f"delete:{key}")
        return 1 if self._data.pop(key, None) is not None else 0


class FailingCache(FakeCredentialCache):
    """A cache double whose every operation raises (the "redis is down" variant)."""

    async def get(self, key: str) -> str | None:
        """Raise instead of answering."""
        raise ConnectionError("redis is down")

    async def set(self, key: str, value: str, ex: int | None = None) -> bool:
        """Raise instead of storing."""
        raise ConnectionError("redis is down")

    async def delete(self, key: str) -> int:
        """Raise instead of deleting."""
        raise ConnectionError("redis is down")


def _vault(key: str = _MASTER_KEY) -> CredentialVault:
    """A vault over a fixed test key (never a real secret)."""
    return CredentialVault(key)


def _repository(tenant_id: str, db: InMemoryDatabase) -> VaultRepository:
    """A vault repository trio scoped to one tenant over a shared database."""
    return VaultRepository(
        credentials=TenantScopedRepository(
            InMemoryRepository[PartnerCredential](db, Collections.PARTNER_CREDENTIALS, PartnerCredential),
            tenant_id,
            Collections.PARTNER_CREDENTIALS,
        ),
        integrations=TenantScopedRepository(
            InMemoryRepository[PartnerIntegration](db, Collections.PARTNER_INTEGRATIONS, PartnerIntegration),
            tenant_id,
            Collections.PARTNER_INTEGRATIONS,
        ),
        templates=TenantScopedRepository(
            InMemoryRepository[IntegrationTemplate](db, Collections.INTEGRATION_TEMPLATES, IntegrationTemplate),
            tenant_id,
            Collections.INTEGRATION_TEMPLATES,
        ),
    )


def _credential(**overrides: object) -> PartnerCredential:
    """A credential row with valid defaults."""
    fields: dict[str, object] = {
        "provider": "github",
        "credential_type": "oauth2",
        "encrypted_data": "v1:ciphertext",
        "key_version": 1,
    }
    fields.update(overrides)
    return PartnerCredential(**fields)  # type: ignore[arg-type]


class TestVaultRoundTrip:
    """AES-GCM envelopes open only under the sealing key and generation."""

    def test_encrypt_decrypt_round_trip(self) -> None:
        """Sealed bytes come back identical (incl. empty and unicode payloads)."""
        vault = _vault()
        for payload in (_SECRET, b"", "tökén-ünïcode".encode()):
            assert vault.decrypt(vault.encrypt(payload, key_version=1), key_version=1) == payload

    def test_same_plaintext_seals_to_different_envelopes(self) -> None:
        """Random nonces keep repeated seals unlinkable; both still open."""
        vault = _vault()
        first = vault.encrypt(_SECRET, key_version=1)
        second = vault.encrypt(_SECRET, key_version=1)
        assert first != second
        assert vault.decrypt(first, key_version=1) == vault.decrypt(second, key_version=1) == _SECRET

    def test_wrong_key_fails_without_leaking_the_secret(self) -> None:
        """A rotated/foreign key reads as undecryptable; the message stays opaque."""
        envelope = _vault().encrypt(_SECRET, key_version=1)
        with pytest.raises(VaultError) as raised:
            _vault(_OTHER_KEY).decrypt(envelope, key_version=1)
        assert _SECRET.decode("utf-8") not in raised.value.public_message
        assert raised.value.error_id

    def test_key_version_rides_the_envelope(self) -> None:
        """The generation is stamped on the envelope and enforced on open."""
        envelope = _vault().encrypt(_SECRET, key_version=2)
        assert envelope.startswith("v2:")
        assert _vault().decrypt(envelope, key_version=2) == _SECRET
        with pytest.raises(VaultError):
            _vault().decrypt(envelope, key_version=1)

    def test_non_positive_versions_fail_closed(self) -> None:
        """Version 0/negative never seals and never opens (caller bug, loud)."""
        with pytest.raises(VaultError):
            _vault().encrypt(_SECRET, key_version=0)
        with pytest.raises(VaultError):
            _vault().decrypt("v1:eA", key_version=0)

    def test_tampered_and_malformed_envelopes_fail(self) -> None:
        """Flipped bytes, bad alphabets, and truncated blobs all read as opaque failures."""
        vault = _vault()
        envelope = vault.encrypt(_SECRET, key_version=1)
        tampered = envelope[:-1] + ("A" if not envelope.endswith("A") else "B")
        for bad in (tampered, "not-an-envelope", "v1:!!!", "v1:eA"):
            with pytest.raises(VaultError):
                vault.decrypt(bad, key_version=1)

    def test_empty_master_key_fails_closed(self) -> None:
        """An unconfigured vault refuses to construct (never an open vault)."""
        with pytest.raises(ConfigurationError) as raised:
            CredentialVault("")
        assert raised.value.details["path"] == "VAULT_MASTER_KEY"


class TestCredentialCache:
    """Redis credential helpers degrade to the store (never raise, never block)."""

    async def test_none_client_degrades(self) -> None:
        """No redis configured: reads miss, writes/invalidations report `False`."""
        assert await get_cached_credential(None, _CREDENTIAL_ID) is None
        assert await set_cached_credential(None, _CREDENTIAL_ID, "v1:blob") is False
        assert await invalidate_credential_cache(None, _CREDENTIAL_ID) is False

    async def test_cache_round_trip_and_invalidate(self) -> None:
        """Set → hit → invalidate → miss, under the namespaced key."""
        cache = FakeCredentialCache()
        assert await set_cached_credential(cache, _CREDENTIAL_ID, "v1:blob") is True
        assert cache.calls[0] == f"set:{CREDENTIAL_CACHE_KEY_PREFIX}{_CREDENTIAL_ID}"
        assert await get_cached_credential(cache, _CREDENTIAL_ID) == "v1:blob"
        assert await invalidate_credential_cache(cache, _CREDENTIAL_ID) is True
        assert await get_cached_credential(cache, _CREDENTIAL_ID) is None

    async def test_empty_envelope_is_never_cached(self) -> None:
        """A caller bug (empty envelope) fails closed instead of caching absence."""
        cache = FakeCredentialCache()
        assert await set_cached_credential(cache, _CREDENTIAL_ID, "") is False
        assert cache.calls == []

    async def test_outage_fails_open_to_the_store(self) -> None:
        """Redis down: every helper logs and degrades (the caller reads the store)."""
        cache = FailingCache()
        assert await get_cached_credential(cache, _CREDENTIAL_ID) is None
        assert await set_cached_credential(cache, _CREDENTIAL_ID, "v1:blob") is False
        assert await invalidate_credential_cache(cache, _CREDENTIAL_ID) is False


class TestVaultModels:
    """Vault rows validate their vault invariants at the model boundary."""

    def test_key_version_floor_is_enforced(self) -> None:
        """`key_version=0` never forms a row (pydantic rejects it first)."""
        with pytest.raises(ValidationError):
            _credential(key_version=0)

    def test_templates_default_to_the_system_tenant(self) -> None:
        """Curated rows read as system-owned until a tenant overrides them."""
        template = IntegrationTemplate(provider="github")
        assert template.tenant_id == SYSTEM_TENANT_ID

    def test_collections_registry_names_the_vault_tables(self) -> None:
        """The four spec 0056 collections exist (additive — existing order kept)."""
        assert Collections.TOOL_EXECUTION_LOGS.value == "tool_execution_logs"
        assert Collections.PARTNER_CREDENTIALS.value == "partner_credentials"
        assert Collections.PARTNER_INTEGRATIONS.value == "partner_integrations"
        assert Collections.INTEGRATION_TEMPLATES.value == "integration_templates"


class TestVaultRepository:
    """Vault state persists per-tenant; foreign rows read as missing."""

    async def test_credential_save_get_and_soft_delete(self) -> None:
        """A saved credential reads back, then reads as missing after delete."""
        repo = _repository("acme", InMemoryDatabase())
        saved = await repo.save_credential(_credential())
        assert saved.id is not None

        assert (await repo.get_credential(saved.id)) is not None
        assert await repo.delete_credential(saved.id) is True
        assert await repo.get_credential(saved.id) is None
        with pytest.raises(NotFoundError):  # deleted rows read as missing (no oracle)
            await repo.delete_credential(saved.id)

    async def test_cross_tenant_credentials_are_invisible(self) -> None:
        """Another tenant's secrets read as missing everywhere (no oracle)."""
        db = InMemoryDatabase()
        acme, globex = _repository("acme", db), _repository("globex", db)
        saved = await acme.save_credential(_credential())

        assert saved.id is not None
        assert await globex.get_credential(saved.id) is None
        assert await globex.list_credentials() == []
        assert [row.provider for row in await acme.find_credentials_by_provider("github")] == ["github"]
        assert await globex.find_credentials_by_provider("github") == []

    async def test_integration_links_list_per_tenant(self) -> None:
        """Links list inside the tenant view only."""
        db = InMemoryDatabase()
        acme, globex = _repository("acme", db), _repository("globex", db)
        await acme.save_integration(PartnerIntegration(provider="slack", agent_id="agent-1", config={"scope": "chat"}))
        assert [row.agent_id for row in await acme.list_integrations()] == ["agent-1"]
        assert await globex.list_integrations() == []

    async def test_template_id_pins_to_provider(self) -> None:
        """Re-seeding a provider is idempotent; system rows stay tenant-invisible."""
        db = InMemoryDatabase()
        system, acme = _repository(SYSTEM_TENANT_ID, db), _repository("acme", db)
        await system.save_template(IntegrationTemplate(provider="github"))

        stored = await system.get_template("github")
        assert stored is not None and stored.id == "github"
        assert await acme.get_template("github") is None
        assert [row.provider for row in await system.list_templates()] == ["github"]


class TestEnvironmentAdditions:
    """The three spec 0056 knobs default safe, bound tight, and load from env."""

    def test_safe_defaults(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Unconfigured: no key, executor dark, 10s budget."""
        for name in ("VAULT_MASTER_KEY", "TOOLS_EXECUTE_ENABLED", "TOOLS_HTTP_TIMEOUT_S"):
            monkeypatch.delenv(name, raising=False)
        env = load_environment(tmp_path / "absent.env")
        assert env.vault_master_key == ""
        assert env.tools_execute_enabled is False
        assert env.tools_http_timeout_s == 10

    def test_timeout_bounds_fail_closed(self) -> None:
        """Budgets outside 1–120 refuse to construct (no unbounded outbound call)."""
        assert Environment(tools_http_timeout_s=120).tools_http_timeout_s == 120
        for bad in (0, -5, 121):
            with pytest.raises(ConfigurationError) as raised:
                Environment(tools_http_timeout_s=bad)
            assert raised.value.details["path"] == "TOOLS_HTTP_TIMEOUT_S"

    def test_values_load_from_the_environment(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """The operator template wires all three knobs through `_collect_values`."""
        monkeypatch.setenv("VAULT_MASTER_KEY", "operator-key")
        monkeypatch.setenv("TOOLS_EXECUTE_ENABLED", "true")
        monkeypatch.setenv("TOOLS_HTTP_TIMEOUT_S", "30")
        env = load_environment(tmp_path / "absent.env")
        assert (env.vault_master_key, env.tools_execute_enabled, env.tools_http_timeout_s) == (
            "operator-key",
            True,
            30,
        )


class TestDcrStub:
    """Phase-3 interface: types exist; undiscoverable issuers fail closed."""

    async def test_register_client_is_pending(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Undiscoverable issuers fail with identifiers only (no fabrication)."""
        from voiceai.modules.tools.errors import ToolExecutionError

        async def _none(_self: object, _issuer: str) -> None:
            return None

        monkeypatch.setattr(DcrService, "discover", _none)
        with pytest.raises(ToolExecutionError):
            await DcrService().register_client("https://issuer.test")

    def test_client_record_carries_its_identifiers(self) -> None:
        """The record shape holds what discovery will return (no secrets by default)."""
        record = ClientRecord(issuer_url="https://issuer.test", client_id="client-1")
        assert (record.issuer_url, record.client_id, record.client_secret) == (
            "https://issuer.test",
            "client-1",
            None,
        )
