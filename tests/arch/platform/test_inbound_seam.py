"""Inbound lookup seam over the platform bridge: tenant-blind, container-wired (spec 0047 seam, spec 0048)."""

from __future__ import annotations

import pytest

from voiceai.common.tenancy import SYSTEM_TENANT_ID, TenantContext, bind_tenant
from voiceai.core.container import build_container, create_auth_store, create_platform_store
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.modules.voice.adapters.inbound_store import PlatformInboundStore
from voiceai.modules.voice.session.inbound import NumberAssignment, resolve_inbound
from voiceai.platform.models import InboundConfig, PhoneNumber


def _bound(tenant_id: str):
    return bind_tenant(TenantContext(tenant_id=tenant_id, request_id="test"))


@pytest.fixture
def bridge():
    database = InMemoryDatabase()
    return create_platform_store(database, create_auth_store(database, None))


async def test_assignments_span_every_tenant(bridge) -> None:
    with _bound("tenant-a"):
        await bridge.save_number(PhoneNumber(number_id="n1", number="+15550000001", assigned_agent_id="agent-1"))
        await bridge.save_number(PhoneNumber(number_id="n2", number="+15550000002"))
        await bridge.save_inbound(InboundConfig(agent_id="agent-1", greeting="hello"))
    with _bound("tenant-b"):
        await bridge.save_number(PhoneNumber(number_id="n3", number="+15550000003", assigned_agent_id="agent-3"))

    seam = PlatformInboundStore(bridge)
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


def test_container_wires_the_seam_over_the_platform_store() -> None:
    container = build_container(
        Environment(app_env="dev", redis_url="", db_backend="memory", allowed_origins=(), cookie_secure=None)
    )
    seam = container.inbound_store()
    assert isinstance(seam, PlatformInboundStore)
