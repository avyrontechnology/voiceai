"""Red-phase tests for POST /calls/place (single real or simulated call)."""

import httpx
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from tests.auth_helpers import signup_owner

from voiceai.platform import create_platform_app
from voiceai.platform.store import MemoryStore


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
        return FakeResponse(200, {"status": "initiated", "dedicated_did": "+911414000000"})


@pytest_asyncio.fixture
async def client():
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        yield ac


async def test_place_simulated_completes_inline(client, monkeypatch):
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    resp = await client.post(
        "/calls/place",
        json={"agent_id": "agent-1", "to_number": "+919800000001", "provider": "simulated", "delay_scale": 0},
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "completed"
    assert body["to_number"] == "+919800000001"


async def test_place_talko_dials_trunk(client, monkeypatch):
    FakeAsyncClient.posted = []
    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    resp = await client.post(
        "/calls/place",
        json={
            "agent_id": "agent-1",
            "to_number": "+919800000001",
            "from_number": "+911414000000",
            "provider": "talko",
            "talko_api_key": "tkp_live_ui",
            "variables": {"student_name": "Aarav Sharma"},
        },
    )
    assert resp.status_code == 202, resp.text
    body = resp.json()
    assert body["status"] == "in_progress"
    assert len(FakeAsyncClient.posted) == 1
    posted_json = FakeAsyncClient.posted[0]["json"]
    assert posted_json["agent_id"] == "agent-1"
    assert posted_json["recipient_phone_number"] == "+919800000001"
    assert posted_json["talko_api_key"] == "tkp_live_ui"


async def test_place_requires_agent_and_number(client):
    resp = await client.post("/calls/place", json={"agent_id": "agent-1"})
    assert resp.status_code == 422
