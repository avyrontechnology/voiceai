"""Contract tests for workflows, campaigns and latency aggregates."""

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from voiceai.platform import create_platform_app
from voiceai.platform.store import MemoryStore


def _definition(**overrides):
    definition = {
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "call", "type": "agent", "label": "Qualify", "config": {"agent_id": "agent-1"}},
            {"id": "grab", "type": "extraction", "config": {"fields": ["customer_name", "interest"]}},
            {"id": "done", "type": "end"},
        ],
        "edges": [],
    }
    definition.update(overrides)
    return definition


@pytest_asyncio.fixture
async def client():
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


async def _make_workflow(client, **overrides):
    payload = {"name": "Outreach", "definition": _definition(**overrides)}
    payload.update({k: v for k, v in overrides.items() if k in ("name",)})
    resp = await client.post("/workflows", json=payload)
    assert resp.status_code == 201, resp.text
    return resp.json()


async def test_workflow_crud_and_versions(client):
    workflow = await _make_workflow(client)
    workflow_id = workflow["workflow_id"]

    assert (await client.get(f"/workflows/{workflow_id}")).status_code == 200
    assert len((await client.get("/workflows")).json()["workflows"]) == 1

    await client.put(f"/workflows/{workflow_id}", json={"name": "Outreach v2"})
    versions = await client.get(f"/workflows/{workflow_id}/versions")
    assert [v["version_number"] for v in versions.json()["versions"]] == [1, 2]

    restore = await client.post(f"/workflows/{workflow_id}/restore/1")
    assert restore.json()["name"] == "Outreach"

    assert (await client.delete(f"/workflows/{workflow_id}")).status_code == 200
    assert (await client.get(f"/workflows/{workflow_id}")).status_code == 404


async def test_workflow_validate_ok_and_errors(client):
    workflow = await _make_workflow(client)
    ok = await client.post(f"/workflows/{workflow['workflow_id']}/validate")
    assert ok.json()["valid"] is True

    bad = await _make_workflow(
        client,
        nodes=[
            {"id": "start", "type": "start"},
            {"id": "call", "type": "agent", "config": {}},
            {"id": "ping", "type": "api", "config": {"method": "POST"}},
        ],
    )
    body = (await client.post(f"/workflows/{bad['workflow_id']}/validate")).json()
    assert body["valid"] is False
    joined = " ".join(body["errors"])
    assert "agent_id" in joined and "url" in joined


async def test_workflow_test_run_happy_path(client):
    workflow = await _make_workflow(client)
    resp = await client.post(
        f"/workflows/{workflow['workflow_id']}/test-run",
        json={"to_number": "+911", "variables": {"customer_name": "Asha"}, "delay_scale": 0},
    )
    assert resp.status_code == 200
    run = resp.json()
    assert run["status"] == "completed"
    kinds = [report["type"] for report in run["reports"]]
    assert kinds == ["start", "agent", "extraction", "end"]
    agent_report = next(r for r in run["reports"] if r["type"] == "agent")
    assert agent_report["status"] == "ok"
    assert agent_report["detail"]["execution_id"]
    extraction_report = next(r for r in run["reports"] if r["type"] == "extraction")
    assert extraction_report["detail"]["values"]["customer_name"] == "Asha"


async def test_workflow_retry_exhausts_and_finishes(client):
    workflow = await _make_workflow(
        client,
        nodes=[
            {"id": "start", "type": "start"},
            {"id": "call", "type": "agent", "config": {"agent_id": "agent-1"}},
            {"id": "again", "type": "retry", "config": {"target_node_id": "call", "max_attempts": 1}},
            {"id": "done", "type": "end"},
        ],
    )
    resp = await client.post(
        f"/workflows/{workflow['workflow_id']}/test-run",
        json={"to_number": "+911", "variables": {"force_outcome": "failed"}, "delay_scale": 0},
    )
    run = resp.json()
    assert run["status"] == "completed"  # reached the end node after exhausting retries
    agent_reports = [r for r in run["reports"] if r["type"] == "agent"]
    assert len(agent_reports) == 2
    assert all(r["status"] == "failed" for r in agent_reports)


async def test_workflow_run_detail_and_404(client):
    workflow = await _make_workflow(client)
    run = (
        await client.post(
            f"/workflows/{workflow['workflow_id']}/test-run",
            json={"to_number": "+911", "delay_scale": 0},
        )
    ).json()
    fetched = await client.get(f"/workflow-runs/{run['run_id']}")
    assert fetched.json()["run_id"] == run["run_id"]
    assert (await client.get("/workflow-runs/nope")).status_code == 404
    assert (await client.post("/workflows/nope/test-run", json={"delay_scale": 0})).status_code == 404


async def test_workflow_campaign_lifecycle(client):
    workflow = await _make_workflow(client)
    create = await client.post(
        "/workflow-campaigns",
        json={
            "workflow_id": workflow["workflow_id"],
            "name": "Morning push",
            "entries": [
                {"to_number": "+911", "variables": {"customer_name": "Asha"}},
                {"to_number": "+912", "variables": {"force_outcome": "no-answer"}},
            ],
            "delay_scale": 0,
        },
    )
    assert create.status_code == 201
    campaign_id = create.json()["campaign_id"]

    start = await client.post(f"/workflow-campaigns/{campaign_id}/start")
    assert start.json()["stats"] == {"total": 2, "completed": 1, "failed": 1, "queued": 0}

    results = await client.get(f"/workflow-campaigns/{campaign_id}/runs")
    assert len(results.json()["runs"]) == 2
    assert (await client.post("/workflow-campaigns/nope/start")).status_code == 404


async def test_latency_aggregates(client):
    empty = await client.get("/executions/latency/summary")
    assert empty.json()["count"] == 0

    await client.post("/calls/simulate", json={"agent_id": "a1", "to_number": "+911", "delay_scale": 0})
    await client.post("/calls/simulate", json={"agent_id": "a1", "to_number": "+912", "delay_scale": 0})

    stats = (await client.get("/executions/latency/summary")).json()
    assert stats["count"] == 2
    assert stats["avg_e2e_ms"] > 0
    assert stats["p50_e2e_ms"] <= stats["p95_e2e_ms"]
    assert stats["by_stage"]["transcriber_ms"] == 180
    assert len(stats["buckets"]) >= 1

    scoped = (await client.get("/executions/latency/summary", params={"agent_id": "other"})).json()
    assert scoped["count"] == 0
