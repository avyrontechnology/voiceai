"""Contract tests for inbound config and the voice library."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from tests.auth_helpers import build_platform_test_app, signup_owner



@pytest_asyncio.fixture
async def client():
    app = build_platform_test_app()
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        yield ac


async def test_inbound_returns_defaults_for_unknown_agent(client):
    resp = await client.get("/api/v1/inbound/agent-1")
    assert resp.status_code == 200
    config = resp.json()
    assert config["agent_id"] == "agent-1"
    assert config["assigned_number_id"] is None
    assert config["blocklist"] == []
    assert config["spam_protection"] is True


async def test_inbound_upsert_roundtrip(client):
    put = await client.put(
        "/api/v1/inbound/agent-1",
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

    get = await client.get("/api/v1/inbound/agent-1")
    assert get.json()["caller_match_source"] == "csv"
    assert get.json()["spam_protection"] is False


async def test_inbound_rejects_unknown_source(client):
    resp = await client.put("/api/v1/inbound/agent-1", json={"caller_match_source": "telepathy"})
    assert resp.status_code == 422
