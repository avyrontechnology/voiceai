"""Contract tests for graph agents: CRUD, versions, validation, dry-run, deploy."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from voiceai.platform import create_platform_app
from voiceai.platform.store import MemoryStore


def _definition(**overrides):
    definition = {
        "agent_information": "You route support calls.",
        "start_node_id": "greet",
        "routing_model": "groq/llama-3.1-8b-instant",
        "nodes": [
            {
                "id": "greet",
                "node_type": "llm",
                "prompt": "Greet the caller.",
                "edges": [{"to_node_id": "triage", "condition_type": "unconditional"}],
            },
            {
                "id": "triage",
                "node_type": "router",
                "edges": [
                    {"to_node_id": "billing", "condition_type": "llm", "condition": "billing question"},
                    {"to_node_id": "bye", "condition_type": "unconditional"},
                ],
            },
            {"id": "billing", "node_type": "static", "static_message": "Billing help is on the way.", "edges": []},
            {"id": "bye", "node_type": "static", "static_message": "Goodbye!", "edges": []},
        ],
    }
    definition.update(overrides)
    return definition


@pytest_asyncio.fixture
async def client():
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _make_graph(client, **overrides):
    resp = await client.post("/graphs", json={"name": "Support", "definition": _definition(**overrides)})
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_graph_crud(client):
    graph = await _make_graph(client)
    graph_id = graph["graph_id"]

    get = await client.get(f"/graphs/{graph_id}")
    assert get.json()["name"] == "Support"

    listed = await client.get("/graphs")
    assert len(listed.json()["graphs"]) == 1

    put = await client.put(f"/graphs/{graph_id}", json={"name": "Support v2"})
    assert put.json()["name"] == "Support v2"

    assert (await client.delete(f"/graphs/{graph_id}")).status_code == 200
    assert (await client.get(f"/graphs/{graph_id}")).status_code == 404


async def test_graph_versions_and_restore(client):
    graph = await _make_graph(client)
    graph_id = graph["graph_id"]

    await client.put(f"/graphs/{graph_id}", json={"name": "v2"})
    await client.put(f"/graphs/{graph_id}", json={"name": "v3"})

    versions = await client.get(f"/graphs/{graph_id}/versions")
    assert [v["version_number"] for v in versions.json()["versions"]] == [1, 2, 3]

    restore = await client.post(f"/graphs/{graph_id}/restore/1")
    assert restore.json()["name"] == "Support"
    versions_after = await client.get(f"/graphs/{graph_id}/versions")
    assert len(versions_after.json()["versions"]) == 4  # pre-restore snapshot kept


async def test_graph_validate_ok(client):
    graph = await _make_graph(client)
    resp = await client.post(f"/graphs/{graph['graph_id']}/validate")
    body = resp.json()
    assert body["valid"] is True
    assert body["errors"] == []


async def test_graph_validate_catches_problems(client):
    graph = await _make_graph(
        client,
        start_node_id="missing",
        nodes=[
            {"id": "a", "node_type": "llm", "edges": [{"to_node_id": "ghost"}]},
            {"id": "a", "node_type": "router", "prompt": "talks", "edges": []},
        ],
    )
    resp = await client.post(f"/graphs/{graph['graph_id']}/validate")
    body = resp.json()
    assert body["valid"] is False
    joined = " ".join(body["errors"])
    assert "missing" in joined and "ghost" in joined and "a" in joined


async def test_graph_dry_run_walks_unconditional_path(client):
    graph = await _make_graph(client)
    resp = await client.post(f"/graphs/{graph['graph_id']}/dry-run")
    body = resp.json()
    # Engine evaluation order: expression first, then llm intent, unconditional last.
    assert body["path"] == ["greet", "triage", "billing"]
    assert body["loop_detected"] is False
    assert any("Billing help" in turn["text"] for turn in body["transcript_preview"])


async def test_graph_dry_run_detects_loop(client):
    graph = await _make_graph(
        client,
        nodes=[
            {
                "id": "greet",
                "node_type": "llm",
                "prompt": "Hi",
                "edges": [{"to_node_id": "greet", "condition_type": "unconditional"}],
            }
        ],
    )
    resp = await client.post(f"/graphs/{graph['graph_id']}/dry-run")
    assert resp.json()["loop_detected"] is True


async def test_graph_deploy_shape(client):
    graph = await _make_graph(client)
    resp = await client.post(f"/graphs/{graph['graph_id']}/deploy", json={"agent_name": "Support Bot"})
    assert resp.status_code == 200
    body = resp.json()
    llm_agent = body["agent_config"]["tasks"][0]["tools_config"]["llm_agent"]
    assert llm_agent["agent_type"] == "graph_agent"
    assert llm_agent["llm_config"]["current_node_id"] == "greet"
    assert len(llm_agent["llm_config"]["nodes"]) == 4
    assert body["agent_config"]["agent_name"] == "Support Bot"
