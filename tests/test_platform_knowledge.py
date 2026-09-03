"""Contract tests for KB detach and per-agent vector-store config."""

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


async def _make_kb(client):
    resp = await client.post(
        "/knowledgebases",
        json={"name": "Refunds", "sources": [{"type": "url", "ref": "https://example.com"}]},
    )
    assert resp.status_code == 201
    return resp.json()["kb_id"]


async def test_kb_detach_roundtrip(client):
    kb_id = await _make_kb(client)
    await client.post(f"/knowledgebases/{kb_id}/attach", json={"agent_id": "agent-1"})

    detach = await client.post(f"/knowledgebases/{kb_id}/detach", json={"agent_id": "agent-1"})
    assert detach.status_code == 200
    assert "agent-1" not in detach.json()["agent_ids"]

    # Idempotent: detaching again still succeeds.
    again = await client.post(f"/knowledgebases/{kb_id}/detach", json={"agent_id": "agent-1"})
    assert again.status_code == 200


async def test_kb_detach_missing_kb(client):
    resp = await client.post("/knowledgebases/nope/detach", json={"agent_id": "agent-1"})
    assert resp.status_code == 404


async def test_vector_config_defaults_and_upsert(client):
    defaults = await client.get("/agents/agent-1/vector-config")
    assert defaults.status_code == 200
    assert defaults.json()["provider"] == "mongodb"

    put = await client.put(
        "/agents/agent-1/vector-config",
        json={
            "provider": "lancedb",
            "vector_id": "support-docs",
            "similarity_top_k": 8,
            "reranker_enabled": True,
            "reranker_model_type": "bge-large",
        },
    )
    assert put.status_code == 200
    assert put.json()["vector_id"] == "support-docs"

    get = await client.get("/agents/agent-1/vector-config")
    assert get.json()["reranker_model_type"] == "bge-large"
    assert get.json()["candidate_count"] == 20


async def test_vector_config_rejects_unknown_provider(client):
    resp = await client.put("/agents/agent-1/vector-config", json={"provider": "pinecone"})
    assert resp.status_code == 422
