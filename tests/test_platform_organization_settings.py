"""Contract tests for organization settings, API keys and workspace reset."""

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


async def test_organization_defaults_and_update(client):
    defaults = await client.get("/api/v1/organization")
    assert defaults.status_code == 200
    assert defaults.json()["data_residency"] == "in"
    assert defaults.json()["notifications"]["low_balance_enabled"] is True

    update = await client.put(
        "/api/v1/organization",
        json={
            "name": "Acme Neural",
            "support_email": "ops@acme.io",
            "data_residency": "us",
            "session_timeout_mins": 60,
            "ip_allowlist": ["10.0.0.0/8"],
            "notifications": {"low_balance_threshold": 25, "channel_webhook": True},
        },
    )
    assert update.status_code == 200
    body = update.json()
    assert body["name"] == "Acme Neural"
    assert body["data_residency"] == "us"
    assert body["notifications"]["low_balance_threshold"] == 25
    assert body["notifications"]["call_failed_enabled"] is True  # untouched default kept


async def test_organization_rejects_bad_values(client):
    assert (await client.put("/api/v1/organization", json={"data_residency": "moon"})).status_code == 422
    assert (await client.put("/api/v1/organization", json={"session_timeout_mins": 1})).status_code == 422
    assert (await client.put("/api/v1/organization", json={"support_email": "nope"})).status_code == 422


async def test_api_keys_create_show_once_and_revoke(client):
    create = await client.post("/api/v1/api-keys", json={"name": "Production"})
    assert create.status_code == 201
    body = create.json()
    assert body["key"].startswith("sk_live_")
    key_id = body["key_id"]

    listed = await client.get("/api/v1/api-keys")
    entries = listed.json()["api_keys"]
    assert len(entries) == 1
    assert "key" not in entries[0]  # full secret never listed again
    assert entries[0]["prefix"] in body["key"]

    assert (await client.delete(f"/api/v1/api-keys/{key_id}")).status_code == 200
    assert (await client.get("/api/v1/api-keys")).json() == {"api_keys": []}
    assert (await client.delete(f"/api/v1/api-keys/{key_id}")).status_code == 404


async def test_workspace_reset_wipes_platform_data(client):
    await client.post("/api/v1/wallet/topup", json={"amount_credits": 100})
    await client.post("/api/v1/batches", json={"agent_id": "a", "name": "x", "entries": [{"to_number": "+911"}]})
    await client.post("/api/v1/sub-accounts", json={"name": "Acme"})

    reset = await client.post("/api/v1/organization/reset")
    assert reset.status_code == 200
    assert reset.json()["state"] == "reset"

    # Spec 0048: the wallet is module-owned money, not platform data — a workspace
    # reset no longer zeroes it.
    assert (await client.get("/api/v1/wallet")).json()["data"]["balance_credits"] == 100
    assert (await client.get("/api/v1/batches")).json() == {"batches": []}
    assert (await client.get("/api/v1/sub-accounts")).json() == {"sub_accounts": []}
    # Organization profile itself survives the reset.
    assert (await client.get("/api/v1/organization")).status_code == 200
