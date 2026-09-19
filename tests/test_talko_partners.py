"""Red-phase tests for per-partner Talko config in DB (no env)."""

import httpx
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from tests.auth_helpers import signup_owner

from voiceai.errors import ConfigurationError
from voiceai.platform import create_platform_app
from voiceai.platform.models import TalkoPartnerConfig
from voiceai.platform.store import MemoryStore
from voiceai.platform.talko_dialer import dial_via_talko, resolve_talko_partner_credentials


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload if payload is not None else {}
        self.text = text

    def json(self):
        return self._payload


class FakeAsyncClient:
    posted = []

    def __init__(self, *args, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, url, json=None, **kwargs):
        FakeAsyncClient.posted.append({"url": url, "json": json})
        return FakeResponse(200, {"status": "initiated"})


@pytest_asyncio.fixture
async def client():
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        yield ac


def _partner(**overrides):
    payload = {
        "partner_id": "2",
        "display_name": "Acme",
        "talko_api_key": "tkp_live_partner",
        "default_did": "+917965263087",
    }
    payload.update(overrides)
    return TalkoPartnerConfig(**payload)


async def test_store_crud_partner():
    store = MemoryStore()
    await store.save_talko_partner(_partner())
    fetched = await store.get_talko_partner("2")
    assert fetched is not None and fetched.talko_api_key == "tkp_live_partner"
    assert fetched.default_did == "+917965263087"  # store keeps raw; router normalizes on write
    assert len(await store.list_talko_partners()) == 1
    assert await store.delete_talko_partner("2") is True
    assert await store.get_talko_partner("2") is None


async def test_resolution_prefers_explicit_over_record(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.posted = []
    store = MemoryStore()
    await store.save_talko_partner(_partner())
    key, did, base = await resolve_talko_partner_credentials(
        store, partner_id="2", explicit_key="tkp_explicit", explicit_did=None, explicit_base=None
    )
    assert key == "tkp_explicit"
    assert did == "917965263087"


async def test_resolution_unknown_partner_raises():
    store = MemoryStore()
    with pytest.raises(ConfigurationError):
        await resolve_talko_partner_credentials(store, partner_id="nope", explicit_key=None,
                                                explicit_did=None, explicit_base=None)


async def test_dial_uses_partner_record(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.posted = []
    store = MemoryStore()
    await store.save_talko_partner(_partner())
    execution = await dial_via_talko(store, agent_id="a", to_number="+919812345678", partner_id="2")
    assert execution.status.value == "in_progress"
    posted = FakeAsyncClient.posted[0]["json"]
    assert posted["talko_api_key"] == "tkp_live_partner"
    assert posted["caller_did"] == "917965263087"
    assert posted["partner_id"] == "2"


async def test_partner_crud_endpoints_mask_key(client):
    create = await client.post("/talko/partners", json={
        "partner_id": "2", "display_name": "Acme",
        "talko_api_key": "tkp_live_partner", "default_did": "+917965263087",
    })
    assert create.status_code == 201, create.text
    body = create.json()
    assert body["partner_id"] == "2"
    assert "tkp_live_partner" not in create.text
    assert body["key_configured"] is True

    listed = await client.get("/talko/partners")
    assert listed.status_code == 200 and len(listed.json()["partners"]) == 1

    dup = await client.post("/talko/partners", json={"partner_id": "2", "talko_api_key": "x"})
    assert dup.status_code == 409

    get = await client.get("/talko/partners/2")
    assert get.status_code == 200 and get.json()["default_did"] == "917965263087"

    missing = await client.get("/talko/partners/99")
    assert missing.status_code == 404

async def test_place_call_with_unknown_partner_is_400(client):
    resp = await client.post("/calls/place", json={
        "agent_id": "agent-1", "to_number": "+9191", "provider": "talko", "partner_id": "nope",
    })
    assert resp.status_code == 400


async def test_dial_rejects_undialable_number_without_http(monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    FakeAsyncClient.posted = []
    store = MemoryStore()
    await store.save_talko_partner(_partner())
    with pytest.raises(ConfigurationError):
        await dial_via_talko(store, agent_id="a", to_number="+91858596675", partner_id="2")
    assert FakeAsyncClient.posted == []


async def test_place_call_with_short_number_is_400(client):
    create = await client.post("/talko/partners", json={"partner_id": "2", "talko_api_key": "k"})
    assert create.status_code == 201, create.text
    resp = await client.post("/calls/place", json={
        "agent_id": "agent-1", "to_number": "+91858596675", "provider": "talko", "partner_id": "2",
    })
    assert resp.status_code == 400
    assert "truncated Indian mobile" in resp.text
