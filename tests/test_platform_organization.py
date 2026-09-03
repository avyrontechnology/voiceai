"""Contract tests for sub-accounts, integrations and the ledger filter."""

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


# --- Sub-accounts ----------------------------------------------------------------------


async def test_sub_account_crud(client):
    create = await client.post("/sub-accounts", json={"name": "Acme EU", "concurrency_cap": 25})
    assert create.status_code == 201
    sub = create.json()
    assert sub["members"] == []
    sub_id = sub["sub_id"]

    listed = await client.get("/sub-accounts")
    assert len(listed.json()["sub_accounts"]) == 1

    get = await client.get(f"/sub-accounts/{sub_id}")
    assert get.json()["concurrency_cap"] == 25

    assert (await client.delete(f"/sub-accounts/{sub_id}")).status_code == 200
    assert (await client.get(f"/sub-accounts/{sub_id}")).status_code == 404


async def test_sub_account_members_upsert_and_remove(client):
    sub_id = (await client.post("/sub-accounts", json={"name": "Acme"})).json()["sub_id"]

    add = await client.post(
        f"/sub-accounts/{sub_id}/members", json={"email": "ops@acme.io", "name": "Ops", "role": "admin"}
    )
    assert add.status_code == 200
    assert add.json()["members"][0]["role"] == "admin"

    # Re-adding the same email updates the role instead of duplicating.
    again = await client.post(f"/sub-accounts/{sub_id}/members", json={"email": "ops@acme.io", "role": "viewer"})
    assert [m for m in again.json()["members"] if m["email"] == "ops@acme.io"][0]["role"] == "viewer"
    assert len(again.json()["members"]) == 1

    remove = await client.delete(f"/sub-accounts/{sub_id}/members", params={"email": "ops@acme.io"})
    assert remove.json()["members"] == []


async def test_sub_account_member_validation(client):
    sub_id = (await client.post("/sub-accounts", json={"name": "Acme"})).json()["sub_id"]
    bad_email = await client.post(f"/sub-accounts/{sub_id}/members", json={"email": "not-an-email", "role": "admin"})
    assert bad_email.status_code == 422
    bad_role = await client.post(f"/sub-accounts/{sub_id}/members", json={"email": "a@b.io", "role": "root"})
    assert bad_role.status_code == 422
    assert (
        await client.post("/sub-accounts/nope/members", json={"email": "a@b.io", "role": "admin"})
    ).status_code == 404


# --- Integrations ----------------------------------------------------------------------


async def test_integration_crud_with_masked_secrets(client):
    create = await client.post(
        "/integrations",
        json={
            "kind": "twilio",
            "name": "Primary SMS",
            "config": {"account_sid": "AC123", "auth_token": "supersecret", "api_key": "key123"},
        },
    )
    assert create.status_code == 201
    body = create.json()
    assert body["config"]["account_sid"] == "AC123"
    assert body["config"]["auth_token"] != "supersecret"
    assert body["config"]["api_key"] != "key123"
    integration_id = body["integration_id"]

    get = await client.get(f"/integrations/{integration_id}")
    assert get.json()["config"]["auth_token"] != "supersecret"

    listed = await client.get("/integrations")
    assert len(listed.json()["integrations"]) == 1

    update = await client.put(f"/integrations/{integration_id}", json={"enabled": False})
    assert update.json()["enabled"] is False
    # Partial config update merges instead of wiping existing keys.
    merged = await client.put(f"/integrations/{integration_id}", json={"config": {"phone_number": "+911"}})
    assert merged.json()["config"]["phone_number"] == "+911"
    assert merged.json()["config"]["account_sid"] == "AC123"

    assert (await client.delete(f"/integrations/{integration_id}")).status_code == 200
    assert (await client.get(f"/integrations/{integration_id}")).status_code == 404


async def test_integration_rejects_unknown_kind(client):
    resp = await client.post("/integrations", json={"kind": "pigeon", "name": "X", "config": {}})
    assert resp.status_code == 422


# --- Ledger filter ----------------------------------------------------------------------


async def test_ledger_type_filter(client):
    await client.post("/wallet/topup", json={"amount_credits": 100})
    all_entries = await client.get("/wallet/ledger")
    assert len(all_entries.json()["entries"]) == 1

    topups = await client.get("/wallet/ledger", params={"type": "topup"})
    assert len(topups.json()["entries"]) == 1
    debits = await client.get("/wallet/ledger", params={"type": "debit"})
    assert debits.json() == {"entries": []}
