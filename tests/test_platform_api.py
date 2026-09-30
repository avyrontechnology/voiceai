"""Phase 0 contract tests for the platform layer.

Covers executions, simulated calls, batches, phone numbers, knowledge bases,
tools, webhooks, wallet and agent templates. All tests run against an
in-memory store so the suite stays offline and deterministic.
"""

import pytest
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


async def _simulate_call(client, **overrides):
    payload = {"agent_id": "agent-1", "to_number": "+911234567890", "delay_scale": 0}
    payload.update(overrides)
    resp = await client.post("/api/v1/calls/simulate", json=payload)
    assert resp.status_code == 202, resp.text
    return resp.json()


# --- Executions & simulated calls -------------------------------------------------


async def test_list_executions_empty(client):
    resp = await client.get("/api/v1/executions")
    assert resp.status_code == 200
    assert resp.json() == {"executions": []}


async def test_simulated_call_completes_inline(client):
    body = await _simulate_call(client)
    assert body["status"] == "completed"
    assert body["agent_id"] == "agent-1"

    resp = await client.get(f"/api/v1/executions/{body['execution_id']}")
    assert resp.status_code == 200
    execution = resp.json()
    assert execution["status"] == "completed"
    assert len(execution["transcript"]) >= 2
    assert execution["latency"]["e2e_ms"] >= 0
    assert execution["duration_s"] >= 0


async def test_simulated_call_carries_variables(client):
    body = await _simulate_call(client, variables={"customer_name": "Asha"})
    resp = await client.get(f"/api/v1/executions/{body['execution_id']}")
    texts = " ".join(turn["text"] for turn in resp.json()["transcript"])
    assert "Asha" in texts


async def test_executions_filter_by_agent(client):
    await _simulate_call(client, agent_id="agent-1")
    await _simulate_call(client, agent_id="agent-2")
    resp = await client.get("/api/v1/executions", params={"agent_id": "agent-1"})
    executions = resp.json()["executions"]
    assert len(executions) == 1
    assert executions[0]["agent_id"] == "agent-1"


async def test_get_execution_not_found(client):
    resp = await client.get("/api/v1/executions/does-not-exist")
    assert resp.status_code == 404


async def test_simulate_call_requires_agent_and_number(client):
    resp = await client.post("/api/v1/calls/simulate", json={"agent_id": "agent-1"})
    assert resp.status_code == 422


# --- Execution stats ----------------------------------------------------------------------


async def test_execution_stats_empty(client):
    resp = await client.get("/api/v1/executions/stats")
    assert resp.status_code == 200
    stats = resp.json()
    assert stats["total"] == 0
    assert stats["by_status"] == {}
    assert stats["avg_e2e_ms"] is None


async def test_execution_stats_aggregates(client):
    await _simulate_call(client, agent_id="agent-1")
    await _simulate_call(client, agent_id="agent-1")
    resp = await client.get("/api/v1/executions/stats")
    stats = resp.json()
    assert stats["total"] == 2
    assert stats["by_status"]["completed"] == 2
    assert stats["avg_e2e_ms"] is not None
    assert stats["avg_e2e_ms"] >= 0
    assert stats["total_duration_s"] >= 0
    assert stats["completed_rate"] == 1.0

    scoped = await client.get("/api/v1/executions/stats", params={"agent_id": "agent-2"})
    assert scoped.json()["total"] == 0


# --- Batches ----------------------------------------------------------------------


async def test_batch_lifecycle(client):
    create = await client.post(
        "/api/v1/batches",
        json={
            "agent_id": "agent-1",
            "name": "COD confirmations",
            "entries": [
                {"to_number": "+911111111111", "variables": {"name": "Asha"}},
                {"to_number": "+912222222222", "variables": {"name": "Ravi"}},
            ],
            "delay_scale": 0,
        },
    )
    assert create.status_code == 201, create.text
    batch = create.json()
    assert batch["status"] == "draft"
    assert batch["stats"]["total"] == 2

    get = await client.get(f"/api/v1/batches/{batch['batch_id']}")
    assert get.status_code == 200

    start = await client.post(f"/api/v1/batches/{batch['batch_id']}/start")
    assert start.status_code == 200
    started = start.json()
    assert started["status"] == "completed"
    assert started["stats"]["completed"] == 2

    executions = await client.get(f"/api/v1/batches/{batch['batch_id']}/executions")
    assert executions.status_code == 200
    assert len(executions.json()["executions"]) == 2


async def test_batch_stop(client):
    create = await client.post(
        "/api/v1/batches",
        json={"agent_id": "agent-1", "name": "STOP", "entries": [{"to_number": "+911111111111"}]},
    )
    batch_id = create.json()["batch_id"]
    stop = await client.post(f"/api/v1/batches/{batch_id}/stop")
    assert stop.status_code == 200
    assert stop.json()["status"] == "stopped"


async def test_batch_requires_entries(client):
    resp = await client.post("/api/v1/batches", json={"agent_id": "agent-1", "name": "empty", "entries": []})
    assert resp.status_code == 422


async def test_batch_rejects_oversized_campaign(client):
    resp = await client.post(
        "/api/v1/batches",
        json={
            "agent_id": "agent-1",
            "name": "huge",
            "entries": [{"to_number": f"+91{i:010d}"} for i in range(1001)],
        },
    )
    assert resp.status_code == 422


async def test_batch_not_found(client):
    assert (await client.get("/api/v1/batches/nope")).status_code == 404
    assert (await client.post("/api/v1/batches/nope/start")).status_code == 404
    assert (await client.post("/api/v1/batches/nope/stop")).status_code == 404
    assert (await client.post("/api/v1/batches/nope/retry-failed")).status_code == 404


async def test_batch_start_rejected_outside_calling_hours(client):
    create = await client.post(
        "/api/v1/batches",
        json={
            "agent_id": "agent-1",
            "name": "night",
            "entries": [{"to_number": "+911111111111"}],
            "calling_hours": {"start": "00:00", "end": "00:00"},
        },
    )
    assert create.status_code == 201
    start = await client.post(f"/api/v1/batches/{create.json()['batch_id']}/start")
    assert start.status_code == 409


async def test_batch_retry_failed(client):
    create = await client.post(
        "/api/v1/batches",
        json={
            "agent_id": "agent-1",
            "name": "flaky",
            "entries": [
                {"to_number": "+911111111111"},
                {"to_number": "+912222222222", "variables": {"force_outcome": "no-answer"}},
            ],
            "delay_scale": 0,
        },
    )
    batch_id = create.json()["batch_id"]
    started = await client.post(f"/api/v1/batches/{batch_id}/start")
    assert started.json()["stats"] == {"total": 2, "queued": 0, "completed": 1, "failed": 1}

    retry = await client.post(f"/api/v1/batches/{batch_id}/retry-failed")
    assert retry.status_code == 201
    retried = retry.json()
    assert len(retried["entries"]) == 1
    assert retried["entries"][0]["to_number"] == "+912222222222"

    restarted = await client.post(f"/api/v1/batches/{retried['batch_id']}/start")
    assert restarted.json()["stats"]["failed"] == 1  # still forced to fail: deterministic


async def test_batch_retry_failed_with_no_failures_conflicts(client):
    create = await client.post(
        "/api/v1/batches",
        json={"agent_id": "agent-1", "name": "clean", "entries": [{"to_number": "+911"}], "delay_scale": 0},
    )
    batch_id = create.json()["batch_id"]
    await client.post(f"/api/v1/batches/{batch_id}/start")
    retry = await client.post(f"/api/v1/batches/{batch_id}/retry-failed")
    assert retry.status_code == 409


# --- Phone numbers ------------------------------------------------------------------


async def test_phone_number_crud_and_assignment(client):
    create = await client.post(
        "/api/v1/phone-numbers", json={"number": "+911234567890", "provider": "simulated", "country": "IN"}
    )
    assert create.status_code == 201
    number_id = create.json()["number_id"]

    assign = await client.post(f"/api/v1/phone-numbers/{number_id}/assign", json={"agent_id": "agent-1"})
    assert assign.status_code == 200
    assert assign.json()["assigned_agent_id"] == "agent-1"

    listed = await client.get("/api/v1/phone-numbers")
    assert len(listed.json()["numbers"]) == 1

    unassign = await client.post(f"/api/v1/phone-numbers/{number_id}/unassign")
    assert unassign.json()["assigned_agent_id"] is None

    delete = await client.delete(f"/api/v1/phone-numbers/{number_id}")
    assert delete.status_code == 200
    assert (await client.get("/api/v1/phone-numbers")).json() == {"numbers": []}


async def test_phone_number_rejects_bad_provider(client):
    resp = await client.post("/api/v1/phone-numbers", json={"number": "+911", "provider": "pigeon"})
    assert resp.status_code == 422


# --- Knowledge bases ------------------------------------------------------------------


async def test_knowledge_base_crud(client):
    create = await client.post(
        "/api/v1/knowledgebases",
        json={"name": "Refund policy", "sources": [{"type": "url", "ref": "https://example.com/refunds"}]},
    )
    assert create.status_code == 201
    kb = create.json()
    assert kb["status"] == "ready"
    kb_id = kb["kb_id"]

    attach = await client.post(f"/api/v1/knowledgebases/{kb_id}/attach", json={"agent_id": "agent-1"})
    assert "agent-1" in attach.json()["agent_ids"]

    assert (await client.get("/api/v1/knowledgebases")).json()["knowledgebases"][0]["kb_id"] == kb_id
    assert (await client.delete(f"/api/v1/knowledgebases/{kb_id}")).status_code == 200


# --- Tools ------------------------------------------------------------------------------


async def test_webhook_crud(client):
    create = await client.post("/api/v1/webhooks", json={"url": "https://example.com/hook", "events": ["call.completed"]})
    assert create.status_code == 201
    hook_id = create.json()["webhook_id"]
    assert (await client.get("/api/v1/webhooks")).json()["webhooks"][0]["webhook_id"] == hook_id
    assert (await client.delete(f"/api/v1/webhooks/{hook_id}")).status_code == 200


# --- Wallet ------------------------------------------------------------------------------


async def test_wallet_topup_and_ledger(client):
    assert (await client.get("/api/v1/wallet")).json()["data"]["balance_credits"] == 0

    topup = await client.post("/api/v1/wallet/topup", json={"amount_credits": 500, "reason": "pilot"})
    assert topup.status_code == 200
    assert topup.json()["data"]["balance_credits"] == 500

    ledger = await client.get("/api/v1/wallet/ledger")
    entries = ledger.json()["data"]["entries"]
    assert len(entries) == 1
    assert entries[0]["amount_credits"] == 500


async def test_wallet_rejects_non_positive_topup(client):
    resp = await client.post("/api/v1/wallet/topup", json={"amount_credits": 0})
    assert resp.status_code == 422


# --- Templates ------------------------------------------------------------------------------


async def test_template_not_found(client):
    assert (await client.get("/api/v1/templates/nope")).status_code == 404
    assert (await client.post("/api/v1/templates/nope/import")).status_code == 404
