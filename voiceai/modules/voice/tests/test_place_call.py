"""Outbound place-call port: service units with DI fakes + controller over the factory (spec 0008).

Service tests drive `VoiceCallService` with an in-memory `VoicePlaceCallRepository`
(real `InMemoryDatabase` generics) and a recording fake outbound port — no network,
no legacy runners. Controller tests drive the REAL stack (controller → service →
repository, fake outbound only) through an httpx ASGI transport off `create_app`,
authenticating via the auth module's owner signup like the auth controller tests.
"""

from __future__ import annotations

import logging
from typing import Any

import pytest
from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.common.errors import NotFoundError
from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.db import InMemoryDatabase
from voiceai.core.environment import Environment
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.voice.errors import PlaceCallError, TalkoPartnerExistsError, UnknownTalkoPartnerError
from voiceai.modules.voice.models import (
    PlacedCall,
    TalkoPartnerConfig,
)
from voiceai.modules.voice.schemas import VoiceContract

CreateTalkoPartnerRequest = VoiceContract.CreateTalkoPartnerRequest
PlaceCallRequest = VoiceContract.PlaceCallRequest
UpdateTalkoPartnerRequest = VoiceContract.UpdateTalkoPartnerRequest
from voiceai.modules.voice.ports.outbound import DialOutcome
from voiceai.modules.voice.repository import VoicePlaceCallRepository
from voiceai.modules.voice.service import VoiceCallService

BASE = "http://place.test"
PREFIX = "/api/v1"
OWNER = {"email": "owner@x.test", "name": "Owner", "password": "owner-pass-1"}


class _FakeOutbound:
    """Recording outbound port: never touches the network."""

    def __init__(self, status: str = "completed") -> None:
        self.calls: list[dict[str, Any]] = []
        self.status = status

    async def dial_trunk_call(self, **kwargs: Any) -> DialOutcome:
        self.calls.append({"kind": "trunk", **kwargs})
        return DialOutcome(
            execution_id="exec-trunk",
            status="in_progress",
            summary="Dialed.",
            from_number=kwargs.get("from_number"),
        )

    async def run_simulated_call_inline(self, **kwargs: Any) -> DialOutcome:
        self.calls.append({"kind": "inline", **kwargs})
        return DialOutcome(execution_id="exec-sim", status="completed", from_number=kwargs.get("from_number"))

    async def start_simulated_call_background(self, **kwargs: Any) -> DialOutcome:
        self.calls.append({"kind": "background", **kwargs})
        return DialOutcome(execution_id="exec-bg", status="queued", from_number=kwargs.get("from_number"))


def _repository() -> VoicePlaceCallRepository:
    """Real repository over throwaway in-memory generics."""
    db = InMemoryDatabase()
    return VoicePlaceCallRepository(
        InMemoryRepository[PlacedCall](db, Collections.EXECUTIONS, PlacedCall),
        InMemoryRepository[TalkoPartnerConfig](db, Collections.TALKO_PARTNERS, TalkoPartnerConfig),
    )


def _service(outbound: _FakeOutbound | None = None) -> tuple[VoiceCallService, _FakeOutbound]:
    """Service wired to a real repository and a fake outbound port."""
    fake = outbound or _FakeOutbound()
    service = VoiceCallService(
        manager_factory=lambda *args, **kwargs: None,  # type: ignore[arg-type] # why: unused by place-call paths
        execution_recorder=lambda *args, **kwargs: None,  # type: ignore[arg-type] # why: same
        logger=logging.getLogger("otobaai.voice.test"),
        place_repository=_repository(),
        outbound=fake,
    )
    return service, fake


async def _partner(service: VoiceCallService, **overrides: Any) -> None:
    """Seed one partner record through the service."""
    payload = {
        "partner_id": "2",
        "display_name": "Acme",
        "talko_api_key": "tkp_live_partner",
        "default_did": "+917965263087",
    }
    payload.update(overrides)
    await service.create_partner(payload=CreateTalkoPartnerRequest(**payload))


# --- service units -----------------------------------------------------------------


async def test_place_call_simulated_inline_completes_and_persists() -> None:
    service, fake = _service()
    placed = await service.place_call(
        payload=PlaceCallRequest(agent_id="agent-1", to_number="+919812345678", delay_scale=0)
    )
    assert placed.status == "completed"
    assert placed.execution_id == "exec-sim"
    assert fake.calls[0]["kind"] == "inline"
    assert fake.calls[0]["variables"] == {}


async def test_place_call_talko_uses_record_credentials() -> None:
    service, fake = _service()
    await _partner(service)
    placed = await service.place_call(
        payload=PlaceCallRequest(
            agent_id="a", to_number="+919812345678", provider="talko", partner_id="2", delay_scale=0
        )
    )
    assert placed.status == "in_progress"
    assert placed.from_number == "917965263087"
    call = fake.calls[0]
    assert call["kind"] == "trunk"
    assert call["talko_api_key"] == "tkp_live_partner"
    assert call["partner_id"] == "2"


async def test_place_call_explicit_beats_record() -> None:
    service, fake = _service()
    await _partner(service)
    await service.place_call(
        payload=PlaceCallRequest(
            agent_id="a",
            to_number="+919812345678",
            provider="talko",
            partner_id="2",
            talko_api_key="tkp_explicit",
            from_number="+911414000000",
            delay_scale=0,
        )
    )
    call = fake.calls[0]
    assert call["talko_api_key"] == "tkp_explicit"
    assert call["from_number"] == "911414000000"  # normalized to digits


async def test_place_call_unknown_partner_fails_closed() -> None:
    service, fake = _service()
    with pytest.raises(UnknownTalkoPartnerError):
        await service.place_call(
            payload=PlaceCallRequest(
                agent_id="a", to_number="+919812345678", provider="talko", partner_id="nope", delay_scale=0
            )
        )
    assert fake.calls == []


async def test_place_call_short_number_never_dials() -> None:
    service, fake = _service()
    with pytest.raises(PlaceCallError):
        await service.place_call(payload=PlaceCallRequest(agent_id="a", to_number="+911", delay_scale=0))
    assert fake.calls == []


async def test_place_call_truncated_indian_number_never_dials() -> None:
    service, fake = _service()
    with pytest.raises(PlaceCallError):
        await service.place_call(
            payload=PlaceCallRequest(agent_id="a", to_number="+91858596675", provider="talko", delay_scale=0)
        )
    assert fake.calls == []


async def test_place_call_unwired_service_fails_closed() -> None:
    service = VoiceCallService(
        manager_factory=lambda *args, **kwargs: None,  # type: ignore[arg-type] # why: unused here
        execution_recorder=lambda *args, **kwargs: None,  # type: ignore[arg-type] # why: unused here
        logger=logging.getLogger("otobaai.voice.test"),
    )
    with pytest.raises(PlaceCallError):
        await service.place_call(payload=PlaceCallRequest(agent_id="a", to_number="+919812345678", delay_scale=0))


async def test_partner_crud_masks_key_and_keeps_it_on_empty_update() -> None:
    service, _ = _service()
    created = await service.create_partner(
        payload=CreateTalkoPartnerRequest(partner_id="2", talko_api_key="tkp_live_partner")
    )
    assert created.key_configured is True
    assert created.key_hint == "tner"
    assert "talko_api_key" not in created.model_dump()
    with pytest.raises(TalkoPartnerExistsError):
        await service.create_partner(payload=CreateTalkoPartnerRequest(partner_id="2", talko_api_key="x"))
    assert len(await service.list_partners()) == 1
    updated = await service.update_partner(partner_id="2", payload=UpdateTalkoPartnerRequest(display_name="Acme"))
    assert updated.display_name == "Acme"
    assert updated.key_configured is True
    assert await service.delete_partner(partner_id="2") is True
    with pytest.raises(NotFoundError):
        await service.get_partner(partner_id="2")


# --- controller over the real factory ----------------------------------------------


async def _authed_client(service: VoiceCallService) -> AsyncClient:
    """Factory app with the voice service replaced; owner session cookie attached."""
    from voiceai.modules.auth.service import AuthService
    from voiceai.modules.auth.tests.conftest import _JWT

    container = build_container(Environment())
    container.voice_call_service.override(providers.Object(service))
    container.auth_service.override(providers.Object(AuthService(container.auth_store(), jwt=_JWT)))
    app = create_app(env=Environment(), container=container)
    client = AsyncClient(transport=ASGITransport(app=app), base_url=BASE)
    signup = await client.post(
        f"{PREFIX}/auth/signup", json={"email": OWNER["email"], "name": OWNER["name"], "password": OWNER["password"]}
    )
    assert signup.status_code == 201, signup.text
    return client


async def test_controller_place_call_simulated_and_partner_crud() -> None:
    service, _ = _service()
    client = await _authed_client(service)

    placed = await client.post(
        f"{PREFIX}/calls/place", json={"agent_id": "agent-1", "to_number": "+919812345678", "delay_scale": 0}
    )
    assert placed.status_code == 202, placed.text
    assert placed.json()["data"]["status"] == "completed"

    created = await client.post(f"{PREFIX}/talko/partners", json={"partner_id": "2", "talko_api_key": "tkp_live_x"})
    assert created.status_code == 201, created.text
    assert created.json()["data"]["key_configured"] is True
    assert "tkp_live_x" not in created.text

    duplicate = await client.post(f"{PREFIX}/talko/partners", json={"partner_id": "2", "talko_api_key": "y"})
    assert duplicate.status_code == 409

    missing = await client.get(f"{PREFIX}/talko/partners/99")
    assert missing.status_code == 404

    short = await client.post(
        f"{PREFIX}/calls/place",
        json={"agent_id": "a", "to_number": "+91858596675", "provider": "talko", "partner_id": "2"},
    )
    assert short.status_code == 400

    unknown = await client.post(
        f"{PREFIX}/calls/place",
        json={"agent_id": "a", "to_number": "+919812345678", "provider": "talko", "partner_id": "nope"},
    )
    assert unknown.status_code == 400

    bad_body = await client.post(f"{PREFIX}/calls/place", json={"agent_id": "agent-1"})
    assert bad_body.status_code == 422


async def test_controller_partner_update_delete_roundtrip() -> None:
    service, _ = _service()
    client = await _authed_client(service)
    await client.post(f"{PREFIX}/talko/partners", json={"partner_id": "2", "talko_api_key": "tkp_live_x"})

    updated = await client.put(f"{PREFIX}/talko/partners/2", json={"display_name": "Acme"})
    assert updated.status_code == 200, updated.text
    assert updated.json()["data"]["display_name"] == "Acme"
    assert updated.json()["data"]["key_configured"] is True

    deleted = await client.delete(f"{PREFIX}/talko/partners/2")
    assert deleted.status_code == 200
    assert (await client.get(f"{PREFIX}/talko/partners/2")).status_code == 404


async def test_controller_routes_are_mounted() -> None:
    """Voice router carries the place-call surface under the API prefix."""
    container = build_container(Environment())
    app = create_app(env=Environment(), container=container)
    paths = {getattr(route, "path", "") for route in app.routes}
    assert {
        "/api/v1/calls/place",
        "/api/v1/talko/partners",
        "/api/v1/talko/partners/{partner_id}",
    } <= paths
