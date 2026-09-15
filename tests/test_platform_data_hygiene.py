"""A4 PLATFORM-DATA hygiene: talko write-only, masking, revoked keys, tenancy, wallet, key hygiene."""

import asyncio
from types import SimpleNamespace
from typing import Any, Dict

import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from tests.auth_helpers import signup_owner

from voiceai.platform import create_platform_app
from voiceai.platform.store import MemoryStore


@pytest_asyncio.fixture
async def ctx() -> Any:
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        await signup_owner(ac)
        yield SimpleNamespace(app=app, client=ac)


def fresh_client(app: Any) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


# --- Talko write-only ------------------------------------------------------------


async def test_batch_talko_key_never_echoed(ctx: Any) -> None:
    create = await ctx.client.post(
        "/batches",
        json={"agent_id": "a", "name": "t", "entries": [{"to_number": "+911"}], "talko_api_key": "tkp_live_secret"},
    )
    assert create.status_code == 201, create.text
    body: Dict[str, Any] = create.json()
    assert body.get("talko_api_key") in (None, ""), f"create echoed secret: {body.keys()}"
    batch_id: str = body["batch_id"]

    get = await ctx.client.get(f"/batches/{batch_id}")
    assert get.status_code == 200
    assert get.json().get("talko_api_key") in (None, "")

    listed = await ctx.client.get("/batches")
    assert listed.status_code == 200
    for row in listed.json()["batches"]:
        assert row.get("talko_api_key") in (None, "")


async def test_batch_talko_key_forwards_to_trunk(ctx: Any, monkeypatch: Any) -> None:
    import httpx

    posted: list = []

    class FakeResponse:
        status_code = 200

        def json(self) -> Dict[str, Any]:
            return {"status": "initiated"}

        text = "ok"

    class FakeClient:
        def __init__(self, *a: Any, **k: Any) -> None:
            pass

        async def __aenter__(self) -> "FakeClient":
            return self

        async def __aexit__(self, *e: Any) -> bool:
            return False

        async def post(self, url: str, json: Any = None, **kw: Any) -> FakeResponse:
            posted.append({"url": url, "json": json, "headers": kw.get("headers")})
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeClient)
    create = await ctx.client.post(
        "/batches",
        json={
            "agent_id": "a",
            "name": "talko",
            "entries": [{"to_number": "+911"}],
            "provider": "talko",
            "talko_api_key": "tkp_live_ui",
        },
    )
    assert create.status_code == 201, create.text
    batch_id = create.json()["batch_id"]
    start = await ctx.client.post(f"/batches/{batch_id}/start")
    assert start.status_code == 202, start.text
    # Background dial pass (202 + poll): wait for the trunk POST to land.
    for _ in range(100):
        if posted:
            break
        await asyncio.sleep(0.05)
    assert posted, "trunk was never dialed"
    assert posted[0]["json"].get("talko_api_key") == "tkp_live_ui"


# --- Vector + integration masking -------------------------------------------------


async def test_vector_connection_string_masked_on_get(ctx: Any) -> None:
    put = await ctx.client.put(
        "/agents/agent-1/vector-config",
        json={"provider": "mongodb", "connection_string": "mongodb://user:pass@host/db"},
    )
    assert put.status_code == 200, put.text
    # PUT response itself must not echo the raw secret.
    assert put.json().get("connection_string") != "mongodb://user:pass@host/db"

    get = await ctx.client.get("/agents/agent-1/vector-config")
    assert get.status_code == 200
    assert get.json().get("connection_string") != "mongodb://user:pass@host/db"
    assert "••••" in (get.json().get("connection_string") or "")


async def test_vector_masked_put_preserves_real(ctx: Any) -> None:
    await ctx.client.put(
        "/agents/agent-1/vector-config",
        json={"provider": "mongodb", "connection_string": "mongodb://real:secret@host/db"},
    )
    masked = (await ctx.client.get("/agents/agent-1/vector-config")).json()["connection_string"]
    assert "••••" in (masked or "")
    # Echo the masked literal back with an unrelated change; real secret must survive.
    put2 = await ctx.client.put(
        "/agents/agent-1/vector-config",
        json={"provider": "mongodb", "connection_string": masked, "db_name": "appdb"},
    )
    assert put2.status_code == 200
    assert "••••" in (put2.json().get("connection_string") or "")
    store: MemoryStore = ctx.app.state.platform_store
    raw = await store.get_vector_config("agent-1")
    assert raw is not None and raw.connection_string == "mongodb://real:secret@host/db"


async def test_integration_masked_put_preserves_secret(ctx: Any) -> None:
    create = await ctx.client.post(
        "/integrations",
        json={"kind": "twilio", "name": "T", "config": {"account_sid": "AC1", "auth_token": "supersecret"}},
    )
    iid = create.json()["integration_id"]
    masked_cfg = (await ctx.client.get(f"/integrations/{iid}")).json()["config"]
    assert masked_cfg["auth_token"] != "supersecret"
    # Echo masked value back; store must keep the real secret.
    upd = await ctx.client.put(f"/integrations/{iid}", json={"config": masked_cfg})
    assert upd.status_code == 200
    store: MemoryStore = ctx.app.state.platform_store
    raw = await store.get_integration(iid)
    assert raw is not None and raw.config.get("auth_token") == "supersecret"


# --- Deleted / disabled user keys -------------------------------------------------


async def _invite_and_accept(ctx: Any, email: str, role: str = "member") -> Dict[str, Any]:
    inv = await ctx.client.post("/auth/invite", json={"email": email, "role": role})
    assert inv.status_code == 201, inv.text
    async with fresh_client(ctx.app) as other:
        acc = await other.post("/auth/accept", json={"token": inv.json()["token"], "password": "correct-horse-2"})
        assert acc.status_code == 201, acc.text
        me = await other.get("/auth/me")
        return {"user": me.json()["user"], "cookies": other.cookies}


async def test_deleted_user_api_keys_revoked(ctx: Any) -> None:
    member = await _invite_and_accept(ctx, "gone@acme.test")
    member_id: str = member["user"]["user_id"]
    # Owner mints a key owned by the member (simulate per-user key via store patch).
    created = await ctx.client.post("/api-keys", json={"name": "mkey", "scopes": ["batches:read"]})
    secret: str = created.json()["key"]
    key_id: str = created.json()["key_id"]
    store: MemoryStore = ctx.app.state.platform_store
    keys = await store.list_api_keys()
    target = next(k for k in keys if k.key_id == key_id)
    target.created_by = member_id
    await store.save_api_key(target)
    # Sanity: key works before delete.
    async with fresh_client(ctx.app) as keyed:
        ok = await keyed.get("/batches", headers={"Authorization": f"Bearer {secret}"})
        assert ok.status_code == 200
    # Delete the user; keys + sessions must die.
    delete = await ctx.client.delete(f"/auth/users/{member_id}")
    assert delete.status_code == 200, delete.text
    async with fresh_client(ctx.app) as keyed2:
        denied = await keyed2.get("/batches", headers={"Authorization": f"Bearer {secret}"})
        assert denied.status_code == 401


async def test_disabled_user_api_key_rejected(ctx: Any) -> None:
    from voiceai.platform.auth import token_hash
    from voiceai.platform.models import ApiKey, new_id, utcnow

    member = await _invite_and_accept(ctx, "sleepy@acme.test")
    member_id = member["user"]["user_id"]
    secret = "sk_test_testdisabledsecret1234567890"
    store: MemoryStore = ctx.app.state.platform_store
    await store.save_api_key(
        ApiKey(
            key_id=new_id("key"),
            name="sleepy",
            prefix="sk_test_te",
            key_hash=token_hash(secret),
            scopes=["batches:read"],
            created_by=member_id,
        )
    )
    # Disable the user directly.
    user = await store.get_user(member_id)
    assert user is not None
    user.disabled = True
    await store.save_user(user)
    async with fresh_client(ctx.app) as keyed:
        denied = await keyed.get("/batches", headers={"Authorization": f"Bearer {secret}"})
        assert denied.status_code == 401


# --- Tenancy starter --------------------------------------------------------------


async def test_batch_org_isolation(ctx: Any) -> None:
    create = await ctx.client.post(
        "/batches", json={"agent_id": "a", "name": "mine", "entries": [{"to_number": "+911"}]}
    )
    assert create.status_code == 201
    assert create.json().get("org_id", "default") == "default"
    # Manually insert a foreign-org batch (simulates a second tenant).
    from voiceai.platform.models import Batch, BatchEntry

    store: MemoryStore = ctx.app.state.platform_store
    foreign = Batch(batch_id="batch_foreign1", agent_id="a", name="theirs", entries=[BatchEntry(to_number="+912")])
    try:
        foreign.org_id = "other"
    except Exception:
        pass
    await store.save_batch(foreign)
    listed = (await ctx.client.get("/batches")).json()["batches"]
    ids = {b["batch_id"] for b in listed}
    assert create.json()["batch_id"] in ids
    assert "batch_foreign1" not in ids
    got = await ctx.client.get("/batches/batch_foreign1")
    assert got.status_code in (403, 404)


# --- Wallet Decimal + atomic -------------------------------------------------------


async def test_wallet_decimal_precision(ctx: Any) -> None:
    await ctx.client.post("/organization/reset")
    # Fresh store: topup 0.1 then 0.2 must equal 0.3 exactly (Decimal), not float dust.
    r1 = await ctx.client.post("/wallet/topup", json={"amount_credits": 0.1})
    assert r1.status_code == 200, r1.text
    r2 = await ctx.client.post("/wallet/topup", json={"amount_credits": 0.2})
    assert r2.status_code == 200, r2.text
    balance = (await ctx.client.get("/wallet")).json()["balance_credits"]
    assert abs(float(balance) - 0.3) < 1e-9
    # Exact decimal check via string to catch 0.30000000000000004.
    assert str(balance) not in ("0.30000000000000004",)


async def test_wallet_concurrent_topups_no_lost_update(ctx: Any) -> None:
    await ctx.client.post("/organization/reset")
    results = await asyncio.gather(*[ctx.client.post("/wallet/topup", json={"amount_credits": 10}) for _ in range(10)])
    assert all(r.status_code == 200 for r in results)
    balance = (await ctx.client.get("/wallet")).json()["balance_credits"]
    assert float(balance) == 100.0


# --- API-key hygiene ---------------------------------------------------------------


async def test_api_key_list_hides_hash(ctx: Any) -> None:
    created = await ctx.client.post("/api-keys", json={"name": "hygiene", "scopes": ["batches:read"]})
    assert created.status_code == 201
    listed = await ctx.client.get("/api-keys")
    assert listed.status_code == 200
    entries = listed.json()["api_keys"]
    assert len(entries) >= 1
    for entry in entries:
        assert entry.get("key_hash") in (None, ""), "hash leaked in list"
        assert "key" not in entry
