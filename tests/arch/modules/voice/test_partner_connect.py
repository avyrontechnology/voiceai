"""Talko connect flow + multi-DID: service units with DI fakes + controller (spec 0009).

The outbound port is faked (no network): fetch returns canned DID payloads or
raises the same errors the bridge raises (bad key, transport failure). Controller
tests drive the real stack through the app factory with an owner session.
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
from voiceai.modules.voice.errors import PlaceCallError
from voiceai.modules.voice.models import (
    ConnectTalkoPartnerRequest,
    PlaceCallRequest,
    PlacedCall,
    TalkoPartnerConfig,
)
from voiceai.modules.voice.ports.outbound import DialOutcome, PartnerPreview
from voiceai.modules.voice.repository import VoicePlaceCallRepository
from voiceai.modules.voice.service import VoiceCallService

BASE = "http://connect.test"
PREFIX = "/api/v1"
OWNER = {"email": "owner@x.test", "name": "Owner", "password": "owner-pass-1"}

FETCH_DIDS = ["917965263087", "917965807203"]


class _FakeOutbound:
    """Recording outbound port with a scripted partner fetch."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.fetch_payload: PartnerPreview = PartnerPreview(partner_id="2", dids=list(FETCH_DIDS))
        self.fetch_error: Exception | None = None

    async def dial_trunk_call(self, **kwargs: Any) -> DialOutcome:
        self.calls.append({"kind": "trunk", **kwargs})
        return DialOutcome(execution_id="exec-trunk", status="in_progress", from_number=kwargs.get("from_number"))

    async def run_simulated_call_inline(self, **kwargs: Any) -> DialOutcome:
        raise AssertionError("not used here")

    async def start_simulated_call_background(self, **kwargs: Any) -> DialOutcome:
        raise AssertionError("not used here")

    async def fetch_partner_dids(self, *, talko_api_key: str, talko_api_base_url: str) -> PartnerPreview:
        self.calls.append({"kind": "fetch", "base": talko_api_base_url, "key_len": len(talko_api_key)})
        if self.fetch_error is not None:
            raise self.fetch_error
        assert "tkp_" not in str(self.calls[-1]), "key must never be logged in fetch bookkeeping"
        return PartnerPreview(partner_id=self.fetch_payload.get("partner_id"), dids=list(self.fetch_payload.get("dids") or []))


def _service(fake: _FakeOutbound | None = None, base_url: str = "https://talko.test/v1") -> tuple[VoiceCallService, _FakeOutbound]:
    """Service wired to a real repository and a fake outbound port."""
    outbound = fake or _FakeOutbound()
    db = InMemoryDatabase()
    service = VoiceCallService(
        manager_factory=lambda *args, **kwargs: None,  # type: ignore[arg-type] # why: unused by connect paths
        execution_recorder=lambda *args, **kwargs: None,  # type: ignore[arg-type] # why: same
        logger=logging.getLogger("otobaai.voice.test"),
        place_repository=VoicePlaceCallRepository(
            InMemoryRepository[PlacedCall](db, Collections.EXECUTIONS, PlacedCall),
            InMemoryRepository[TalkoPartnerConfig](db, Collections.TALKO_PARTNERS, TalkoPartnerConfig),
        ),
        outbound=outbound,
        talko_service_base_url=base_url,
    )
    return service, outbound


async def test_preview_normalizes_and_derives_partner() -> None:
    service, fake = _service()
    preview = await service.preview_partner(talko_api_key="tkp_live_x")
    assert preview.partner_id == "2"
    assert preview.dids == FETCH_DIDS
    assert fake.calls[0]["base"] == "https://talko.test/v1"
    assert fake.calls[0]["key_len"] == len("tkp_live_x")


async def test_preview_empty_dids_yields_no_partner() -> None:
    service, fake = _service()
    fake.fetch_payload = PartnerPreview(partner_id=None, dids=[])
    preview = await service.preview_partner(talko_api_key="tkp_live_x")
    assert preview.partner_id is None
    assert preview.dids == []


async def test_preview_bad_key_is_client_error() -> None:
    service, fake = _service()
    fake.fetch_error = PlaceCallError("Invalid Talko partner key.")
    with pytest.raises(PlaceCallError):
        await service.preview_partner(talko_api_key="wrong")


async def test_preview_without_base_url_fails_closed() -> None:
    service, _ = _service(base_url="")
    with pytest.raises(PlaceCallError):
        await service.preview_partner(talko_api_key="tkp_live_x")


async def test_connect_upserts_with_first_did_default() -> None:
    service, _ = _service()
    view = await service.connect_partner(payload=ConnectTalkoPartnerRequest(talko_api_key="tkp_live_x"))
    assert view.partner_id == "2"
    assert view.dids == FETCH_DIDS
    assert view.default_did == FETCH_DIDS[0]
    assert view.key_configured is True
    assert "tkp_live_x" not in view.model_dump_json()


async def test_connect_needs_explicit_partner_when_empty() -> None:
    service, fake = _service()
    fake.fetch_payload = PartnerPreview(partner_id=None, dids=[])
    with pytest.raises(PlaceCallError):
        await service.connect_partner(payload=ConnectTalkoPartnerRequest(talko_api_key="tkp_live_x"))
    view = await service.connect_partner(
        payload=ConnectTalkoPartnerRequest(talko_api_key="tkp_live_x", partner_id="9")
    )
    assert view.partner_id == "9"
    assert view.default_did is None


async def test_refresh_merges_additively_and_keeps_default() -> None:
    service, fake = _service()
    await service.connect_partner(payload=ConnectTalkoPartnerRequest(talko_api_key="tkp_live_x"))
    fake.fetch_payload = PartnerPreview(partner_id="2", dids=["917965263087", "919999999999"])
    view = await service.refresh_partner_dids(partner_id="2")
    assert view.dids == ["917965263087", "917965807203", "919999999999"]
    assert view.default_did == "917965263087"
    with pytest.raises(NotFoundError):
        await service.refresh_partner_dids(partner_id="nope")


async def test_place_call_rejects_foreign_did() -> None:
    service, _ = _service()
    await service.connect_partner(payload=ConnectTalkoPartnerRequest(talko_api_key="tkp_live_x"))
    with pytest.raises(PlaceCallError):
        await service.place_call(
            payload=PlaceCallRequest(
                agent_id="a",
                to_number="+919812345678",
                provider="talko",
                partner_id="2",
                from_number="+911414000000",
                delay_scale=0,
            )
        )


async def test_place_call_accepts_member_did() -> None:
    service, fake = _service()
    await service.connect_partner(payload=ConnectTalkoPartnerRequest(talko_api_key="tkp_live_x"))
    placed = await service.place_call(
        payload=PlaceCallRequest(
            agent_id="a",
            to_number="+919812345678",
            provider="talko",
            partner_id="2",
            from_number="+917965807203",
            delay_scale=0,
        )
    )
    assert placed.from_number == "917965807203"
    assert fake.calls[-1]["from_number"] == "917965807203"


async def _authed_client(service: VoiceCallService) -> AsyncClient:
    """Factory app with the voice service replaced; owner session cookie attached."""
    container = build_container(Environment())
    container.voice_call_service.override(providers.Object(service))
    app = create_app(env=Environment(), container=container)
    client = AsyncClient(transport=ASGITransport(app=app), base_url=BASE)
    signup = await client.post(
        f"{PREFIX}/auth/signup", json={"email": OWNER["email"], "name": OWNER["name"], "password": OWNER["password"]}
    )
    assert signup.status_code == 201, signup.text
    return client


async def test_controller_preview_connect_refresh_roundtrip() -> None:
    service, _ = _service()
    client = await _authed_client(service)

    preview = await client.post(f"{PREFIX}/talko/partners/preview", json={"talko_api_key": "tkp_live_x"})
    assert preview.status_code == 200, preview.text
    assert preview.json()["data"]["dids"] == FETCH_DIDS
    assert "tkp_live_x" not in preview.text

    connect = await client.post(f"{PREFIX}/talko/partners/connect", json={"talko_api_key": "tkp_live_x"})
    assert connect.status_code == 201, connect.text
    assert connect.json()["data"]["default_did"] == FETCH_DIDS[0]

    refresh = await client.post(f"{PREFIX}/talko/partners/2/refresh")
    assert refresh.status_code == 200, refresh.text
    assert refresh.json()["data"]["dids"] == FETCH_DIDS

    missing = await client.post(f"{PREFIX}/talko/partners/99/refresh")
    assert missing.status_code == 404
