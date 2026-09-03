"""Contract tests for inbound config and the voice library."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from voiceai.platform import create_platform_app
from voiceai.platform.store import MemoryStore


@pytest_asyncio.fixture
async def client():
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def test_inbound_returns_defaults_for_unknown_agent(client):
    resp = await client.get("/inbound/agent-1")
    assert resp.status_code == 200
    config = resp.json()
    assert config["agent_id"] == "agent-1"
    assert config["assigned_number_id"] is None
    assert config["blocklist"] == []
    assert config["spam_protection"] is True


async def test_inbound_upsert_roundtrip(client):
    put = await client.put(
        "/inbound/agent-1",
        json={
            "assigned_number_id": "num_1",
            "greeting": "Thanks for calling Acme!",
            "spam_protection": False,
            "caller_match_source": "csv",
            "caller_match_ref": "customers.csv",
            "blocklist": ["+91111", "+91222"],
        },
    )
    assert put.status_code == 200
    assert put.json()["blocklist"] == ["+91111", "+91222"]

    get = await client.get("/inbound/agent-1")
    assert get.json()["caller_match_source"] == "csv"
    assert get.json()["spam_protection"] is False


async def test_inbound_rejects_unknown_source(client):
    resp = await client.put("/inbound/agent-1", json={"caller_match_source": "telepathy"})
    assert resp.status_code == 422


async def test_voice_library_crud(client):
    create = await client.post(
        "/voices",
        json={
            "agent_id": "agent-1",
            "name": "Asha (cloned)",
            "provider": "elevenlabs",
            "provider_voice_id": "abc123",
            "source": "cloned",
            "language": "hi",
        },
    )
    assert create.status_code == 201
    voice_id = create.json()["voice_id"]

    scoped = await client.get("/voices", params={"agent_id": "agent-1"})
    assert len(scoped.json()["voices"]) == 1

    other = await client.get("/voices", params={"agent_id": "agent-2"})
    assert other.json() == {"voices": []}

    assert (await client.delete(f"/voices/{voice_id}")).status_code == 200
    assert (await client.delete(f"/voices/{voice_id}")).status_code == 404


async def test_voice_rejects_unknown_source(client):
    resp = await client.post(
        "/voices",
        json={"name": "X", "provider": "elevenlabs", "provider_voice_id": "v", "source": "grown"},
    )
    assert resp.status_code == 422
