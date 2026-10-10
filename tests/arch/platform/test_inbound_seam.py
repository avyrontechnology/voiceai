"""Inbound lookup seam over the numbers module: tenant-blind, container-wired (spec 0047 seam, rewired by 0074)."""

from __future__ import annotations

import pytest

from voiceai.common.tenancy import SYSTEM_TENANT_ID, TenantContext, bind_tenant
from voiceai.core.container import _build_inbound_store, build_container, create_auth_store, create_platform_store
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.numbers.models import PhoneNumber
from voiceai.modules.numbers.repository import PhoneNumberRepository
from voiceai.modules.voice.adapters.inbound_store import NumbersInboundStore
from voiceai.modules.voice.session.inbound import NumberAssignment, resolve_inbound
from voiceai.platform.models import InboundConfig


def _bound(tenant_id: str):
    return bind_tenant(TenantContext(tenant_id=tenant_id, request_id="test"))


@pytest.fixture
def database() -> InMemoryDatabase:
    return InMemoryDatabase()


@pytest.fixture
def bridge(database: InMemoryDatabase):
    return create_platform_store(database, create_auth_store(database, None))


def _numbers(database: InMemoryDatabase) -> PhoneNumberRepository:
    """The numbers repository, unscoped — as the inbound seam is wired in production."""
    return PhoneNumberRepository(
        InMemoryRepository(database, Collections.PLATFORM_NUMBERS, PhoneNumber)
    )


async def test_assignments_span_every_tenant(database: InMemoryDatabase, bridge) -> None:
    """The webhook path sees every tenant's numbers, not just the bound one.

    An inbound carrier webhook arrives before any tenant is known, so a
    tenant-scoped lookup here would silently drop most of the world's calls.
    """
    numbers = _numbers(database)
    with _bound("tenant-a"):
        await numbers.create(PhoneNumber(number="+15550000001", assigned_agent_id="agent-1"))
        await numbers.create(PhoneNumber(number="+15550000002"))
        await bridge.save_inbound(InboundConfig(agent_id="agent-1", greeting="hello"))
    with _bound("tenant-b"):
        await numbers.create(PhoneNumber(number="+15550000003", assigned_agent_id="agent-3"))

    seam = NumbersInboundStore(numbers, bridge)
    with _bound(SYSTEM_TENANT_ID):  # the webhook path binds the system tenant
        assignments = await seam.list_assignments()
        assert sorted(assignments, key=lambda row: row.number) == [
            NumberAssignment(number="+15550000001", assigned_agent_id="agent-1"),
            NumberAssignment(number="+15550000002", assigned_agent_id=None),
            NumberAssignment(number="+15550000003", assigned_agent_id="agent-3"),
        ]
        config = await seam.get_inbound_config("agent-1")
        assert config is not None and config["greeting"] == "hello"
        assert await seam.get_inbound_config("agent-9") is None
        resolved = await resolve_inbound("+1 (555) 000-0003", seam)
        assert resolved is not None and resolved[0] == "agent-3"
        assert await resolve_inbound("+15550000002", seam) is None  # unassigned rejects


async def test_legacy_envelope_rows_still_resolve(database: InMemoryDatabase, bridge) -> None:
    """A number written before spec 0074 keeps resolving for inbound calls."""
    raw = InMemoryRepository(database, Collections.PLATFORM_NUMBERS, PhoneNumber)
    await raw.insert(
        PhoneNumber(id="legacy-1", number="+15550000009", assigned_agent_id="agent-legacy")
    )

    seam = NumbersInboundStore(_numbers(database), bridge)

    resolved = await resolve_inbound("+15550000009", seam)
    assert resolved is not None and resolved[0] == "agent-legacy"


def test_container_wires_the_seam_over_the_numbers_module() -> None:
    """The container builds the seam from the numbers repository, not the platform store."""
    container = build_container(
        Environment(app_env="dev", redis_url="", db_backend="memory", allowed_origins=(), cookie_secure=None)
    )

    seam = container.inbound_store()

    assert isinstance(seam, NumbersInboundStore)


def test_the_builder_tolerates_no_ambient_tenant() -> None:
    """The seam builds with no tenant bound — the webhook case (spec 0074)."""
    database = InMemoryDatabase()

    seam = _build_inbound_store(database, create_platform_store(database, create_auth_store(database, None)))

    assert isinstance(seam, NumbersInboundStore)
