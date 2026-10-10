"""Integrations module units: the credential masking contract and the merge guard.

These are the module's security tests. `mask_secrets` decides what leaves the
process and `merge_config` decides what a client can write without destroying
what it was never shown; both are pinned here, including the round-trip that used
to silently overwrite a stored API key with the mask marker.
"""

from __future__ import annotations

from typing import Any

from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.integrations.constants import MASKED_SECRET
from voiceai.modules.integrations.models import Integration
from voiceai.modules.integrations.repository import IntegrationRepository, as_integration
from voiceai.modules.integrations.service import IntegrationsService
from voiceai.modules.integrations.static_methods import is_secret_field, mask_secrets, merge_config

REAL_KEY = "sk-live-9f3a2b7c4d1e"


def _service() -> tuple[IntegrationsService, IntegrationRepository]:
    db = InMemoryDatabase()
    repository = IntegrationRepository(InMemoryRepository(db, Collections.PLATFORM_INTEGRATIONS, Integration))
    return IntegrationsService(repository), repository


# --- masking -----------------------------------------------------------------------------


def test_secret_field_names_are_recognised() -> None:
    """A field is secret when its name carries any hint, in any case."""
    for field in ("api_key", "API_KEY", "authToken", "password", "client_secret"):
        assert is_secret_field(field) is True, field
    assert is_secret_field("region") is False
    assert is_secret_field("name") is False


def test_masking_hides_string_secrets() -> None:
    """A string secret never leaves the process."""
    masked = mask_secrets({"api_key": REAL_KEY, "account_sid": "AC123", "from": "+15551234567"})

    assert masked["api_key"] == MASKED_SECRET
    assert masked["account_sid"] == "AC123"  # "sid" is not a hint — legacy behaviour
    assert masked["from"] == "+15551234567"


def test_masking_leaves_non_strings_alone() -> None:
    """A nested structure under a secret-looking name is not mangled."""
    nested: dict[str, Any] = {"inner": {"key": REAL_KEY}}
    ports: list[int] = [443, 8443]

    masked = mask_secrets({"token_config": nested, "key_ports": ports})

    assert masked["token_config"] == nested
    assert masked["key_ports"] == ports


def test_masking_does_not_mutate_the_stored_config() -> None:
    """The caller's dict is untouched, so the stored secret survives."""
    stored = {"api_key": REAL_KEY}

    mask_secrets(stored)

    assert stored["api_key"] == REAL_KEY


def test_masked_returns_a_copy() -> None:
    """`Integration.masked()` leaves the receiver's credentials intact."""
    integration = Integration(id="i1", kind="twilio", name="main", config={"api_key": REAL_KEY})

    masked = integration.masked()

    assert masked.config["api_key"] == MASKED_SECRET
    assert integration.config["api_key"] == REAL_KEY


# --- the merge guard ---------------------------------------------------------------------


def test_merge_keeps_a_stored_secret_when_the_mask_is_echoed_back() -> None:
    """The regression this module exists to prevent.

    Read an integration, get `••••••••` back for its API key, PUT the response
    you just read. Under a plain `{**stored, **incoming}` the stored credential
    is overwritten with the mask — unrecoverably.
    """
    stored = {"api_key": REAL_KEY, "account_sid": "AC123"}

    merged = merge_config(stored, {"api_key": MASKED_SECRET, "account_sid": "AC123"})

    assert merged["api_key"] == REAL_KEY


def test_merge_still_applies_a_genuinely_new_key() -> None:
    """The guard skips only the marker; real updates still land."""
    stored = {"api_key": REAL_KEY}

    merged = merge_config(stored, {"api_key": MASKED_SECRET, "timeout": 30})

    assert merged["api_key"] == REAL_KEY
    assert merged["timeout"] == 30


def test_merge_applies_a_real_secret_change() -> None:
    """Rotating a credential works — only the marker is ignored."""
    merged = merge_config({"api_key": REAL_KEY}, {"api_key": "sk-live-ROTATED"})

    assert merged["api_key"] == "sk-live-ROTATED"


# --- service: every read is masked --------------------------------------------------------


async def test_create_returns_masked_but_stores_raw() -> None:
    """The response hides the secret; the store keeps it."""
    service, repository = _service()

    created = await service.create(kind="twilio", name="main", config={"api_key": REAL_KEY})

    assert created.config["api_key"] == MASKED_SECRET
    stored = await repository.get(created.id or "")
    assert stored is not None
    assert stored.config["api_key"] == REAL_KEY


async def test_list_returns_masked() -> None:
    """The listing would otherwise leak every credential in one response."""
    service, _ = _service()
    await service.create(kind="twilio", name="main", config={"api_key": REAL_KEY})

    listed = await service.list_all()

    assert listed[0].config["api_key"] == MASKED_SECRET


async def test_get_returns_masked() -> None:
    """A single read is masked too."""
    service, _ = _service()
    created = await service.create(kind="twilio", name="main", config={"api_key": REAL_KEY})

    fetched = await service.get(created.id or "")

    assert fetched.config["api_key"] == MASKED_SECRET


async def test_update_returns_masked() -> None:
    """The update response hides the secret too."""
    service, _ = _service()
    created = await service.create(kind="twilio", name="main", config={"api_key": REAL_KEY})

    updated = await service.update(created.id or "", name="renamed")

    assert updated.config["api_key"] == MASKED_SECRET
    assert updated.name == "renamed"


async def test_a_read_update_write_round_trip_preserves_the_secret() -> None:
    """The whole bug, end to end: GET, echo back, PUT.

    A client that reads an integration and writes it straight back must not
    destroy the credential it was never shown (spec 0077).
    """
    service, repository = _service()
    created = await service.create(kind="twilio", name="main", config={"api_key": REAL_KEY})
    read_back = await service.get(created.id or "")

    await service.update(
        created.id or "",
        name=read_back.name,
        config=read_back.config,  # the masked view, echoed verbatim
        enabled=read_back.enabled,
    )

    stored = await repository.get(created.id or "")
    assert stored is not None
    assert stored.config["api_key"] == REAL_KEY


# --- service: partial update --------------------------------------------------------------


async def test_update_leaves_omitted_fields_alone() -> None:
    """A partial update touches only what it names."""
    service, _ = _service()
    created = await service.create(kind="twilio", name="main", config={"api_key": REAL_KEY}, enabled=True)

    updated = await service.update(created.id or "", name="renamed")

    assert updated.name == "renamed"
    assert updated.enabled is True


async def test_update_merges_config_per_key() -> None:
    """Config merges rather than replacing wholesale."""
    service, repository = _service()
    created = await service.create(kind="twilio", name="main", config={"api_key": REAL_KEY, "region": "us1"})

    await service.update(created.id or "", config={"region": "us2"})

    stored = await repository.get(created.id or "")
    assert stored is not None
    assert stored.config == {"api_key": REAL_KEY, "region": "us2"}


async def test_update_bumps_updated_at() -> None:
    """A write is observable in the timestamps."""
    service, repository = _service()
    created = await service.create(kind="twilio", name="main", config={})
    before = (await repository.get(created.id or "")).updated_at  # type: ignore[union-attr]

    await service.update(created.id or "", name="renamed")

    after = (await repository.get(created.id or "")).updated_at  # type: ignore[union-attr]
    assert after >= before


# --- storage ------------------------------------------------------------------------------


async def test_list_all_walks_every_row() -> None:
    """Listing is a full walk, not a capped first page."""
    db = InMemoryDatabase()
    repository = IntegrationRepository(InMemoryRepository(db, Collections.PLATFORM_INTEGRATIONS, Integration))
    for index in range(120):
        await repository.create(Integration(kind="twilio", name=f"int-{index}"))

    assert len(await repository.list_all()) == 120


def test_a_legacy_envelope_row_still_reads() -> None:
    """A row written before the migration reads as a typed integration."""
    row = {
        "id": "i1",
        "payload": {"integration_id": "i1", "kind": "twilio", "name": "main", "config": {"api_key": REAL_KEY}},
    }

    record = as_integration(row)

    assert record is not None
    assert record.id == "i1"
    assert record.config["api_key"] == REAL_KEY


def test_an_unreadable_row_is_a_miss() -> None:
    """A row we cannot read is a miss, not a crash."""
    assert as_integration({"payload": {"nothing": 1}}) is None


async def test_another_tenants_integration_is_not_visible() -> None:
    """The scoped repository keeps tenants apart (spec 0020, M1b).

    Pinned for every migrated family: a credential one tenant cannot see is a
    credential they re-enter, and one tenant seeing another's is worse.
    """
    from voiceai.common.tenancy import TenantContext, bind_tenant
    from voiceai.core.container import _scoped_collection

    db = InMemoryDatabase()

    def scoped() -> IntegrationRepository:
        return IntegrationRepository(_scoped_collection(db, Collections.PLATFORM_INTEGRATIONS, Integration))

    with bind_tenant(TenantContext(tenant_id="tenant-a", request_id="req-1")):
        await scoped().create(Integration(kind="twilio", name="a", config={"api_key": REAL_KEY}))
    with bind_tenant(TenantContext(tenant_id="tenant-b", request_id="req-2")):
        assert await scoped().list_all() == []
        assert await scoped().get("missing") is None
    with bind_tenant(TenantContext(tenant_id="tenant-a", request_id="req-3")):
        assert len(await scoped().list_all()) == 1
