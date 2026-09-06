"""Contract tests for self-hosted auth: signup/login, invites, RBAC, keys.

Runs against the platform app with MemoryStore (no engine deps).
Execute with --noconftest: the shared conftest pulls the engine chain,
which minimal envs do not install.
"""

from types import SimpleNamespace

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from voiceai.platform import create_platform_app
from voiceai.platform.store import MemoryStore


@pytest_asyncio.fixture
async def ctx():
    app = create_platform_app(MemoryStore())
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield SimpleNamespace(app=app, client=client)


def fresh_client(app):
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test")


async def signup_owner(client, email="owner@acme.test"):
    resp = await client.post(
        "/auth/signup", json={"email": email, "name": "Owner", "password": "correct-horse-1"}
    )
    assert resp.status_code == 201, resp.text
    assert resp.json()["role"] == "owner"
    return resp.json()


async def test_signup_closes_after_first_user(ctx):
    await signup_owner(ctx.client)
    resp = await ctx.client.post(
        "/auth/signup", json={"email": "second@acme.test", "password": "correct-horse-1"}
    )
    assert resp.status_code == 403


async def test_login_and_me_roundtrip(ctx):
    await signup_owner(ctx.client)
    me = await ctx.client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["user"]["email"] == "owner@acme.test"
    assert "*" in me.json()["scopes"]

    # Wrong password is a flat 401 (no user enumeration).
    bad = await ctx.client.post(
        "/auth/login", json={"email": "owner@acme.test", "password": "wrong-password"}
    )
    assert bad.status_code == 401


async def test_me_without_session_is_401(ctx):
    await signup_owner(ctx.client)
    async with fresh_client(ctx.app) as anon:
        assert (await anon.get("/auth/me")).status_code == 401
        assert (await anon.get("/tools")).status_code == 401


async def test_invite_accept_and_role_gates(ctx):
    await signup_owner(ctx.client)

    invite = await ctx.client.post(
        "/auth/invite", json={"email": "member@acme.test", "role": "member"}
    )
    assert invite.status_code == 201, invite.text
    token = invite.json()["token"]

    async with fresh_client(ctx.app) as member:
        accepted = await member.post(
            "/auth/accept", json={"token": token, "name": "Member", "password": "correct-horse-2"}
        )
        assert accepted.status_code == 201, accepted.text
        assert accepted.json()["user"]["role"] == "member"

        # Members write platform resources...
        tool = await member.post(
            "/tools", json={"name": "clock", "kind": "datetime", "config": {"timezone": "Asia/Kolkata"}}
        )
        assert tool.status_code == 201, tool.text
        # ...but cannot manage users or keys.
        assert (await member.post("/auth/invite", json={"email": "x@y.test"})).status_code in (401, 403)
        assert (await member.get("/auth/users")).status_code == 403
        assert (await member.post("/api-keys", json={"name": "k"})).status_code == 403

    # Reusing the token fails.
    async with fresh_client(ctx.app) as other:
        again = await other.post(
            "/auth/accept", json={"token": token, "password": "correct-horse-3"}
        )
        assert again.status_code == 400


async def test_non_owner_cannot_invite_admin(ctx):
    await signup_owner(ctx.client)
    invite = await ctx.client.post(
        "/auth/invite", json={"email": "admin@acme.test", "role": "admin"}
    )
    token = invite.json()["token"]
    async with fresh_client(ctx.app) as admin:
        await admin.post("/auth/accept", json={"token": token, "password": "correct-horse-6"})
        # Admins may invite members but never owners/admins.
        assert (
            await admin.post("/auth/invite", json={"email": "o2@acme.test", "role": "owner"})
        ).status_code == 403
        ok = await admin.post("/auth/invite", json={"email": "m2@acme.test", "role": "member"})
        assert ok.status_code == 201


async def test_viewer_is_read_only(ctx):
    await signup_owner(ctx.client)
    invite = await ctx.client.post(
        "/auth/invite", json={"email": "viewer@acme.test", "role": "viewer"}
    )
    token = invite.json()["token"]
    async with fresh_client(ctx.app) as viewer:
        accepted = await viewer.post(
            "/auth/accept", json={"token": token, "password": "correct-horse-4"}
        )
        assert accepted.status_code == 201
        assert (await viewer.get("/tools")).status_code == 200
        assert (await viewer.get("/batches")).status_code == 200
        assert (await viewer.post("/tools", json={"name": "t", "kind": "datetime"})).status_code == 403
        assert (
            await viewer.post(
                "/calls/simulate",
                json={"agent_id": "a", "to_number": "+911234567890"},
            )
        ).status_code == 403
        assert (await viewer.post("/auth/ws-ticket")).status_code == 403


async def test_api_key_scopes(ctx):
    await signup_owner(ctx.client)
    created = await ctx.client.post("/api-keys", json={"name": "ci", "scopes": ["batches:read"]})
    assert created.status_code == 201, created.text
    secret = created.json()["key"]
    assert secret.startswith(created.json()["prefix"])

    async with fresh_client(ctx.app) as keyed:
        ok = await keyed.get("/batches", headers={"Authorization": f"Bearer {secret}"})
        assert ok.status_code == 200
        denied = await keyed.post(
            "/batches",
            json={"agent_id": "a", "name": "n", "entries": [{"to_number": "+91"}]},
            headers={"Authorization": f"Bearer {secret}"},
        )
        assert denied.status_code == 403
        bad = await keyed.get("/batches", headers={"Authorization": "Bearer nope"})
        assert bad.status_code == 401


async def test_reset_is_owner_only_and_preserves_users(ctx):
    await signup_owner(ctx.client)
    invite = await ctx.client.post(
        "/auth/invite", json={"email": "member@acme.test", "role": "member"}
    )
    token = invite.json()["token"]
    async with fresh_client(ctx.app) as member:
        await member.post("/auth/accept", json={"token": token, "password": "correct-horse-2"})
        assert (await member.post("/organization/reset")).status_code == 403

    wiped = await ctx.client.post("/organization/reset")
    assert wiped.status_code == 200
    assert (await ctx.client.get("/auth/me")).status_code == 200
    users = await ctx.client.get("/auth/users")
    assert users.status_code == 200
    assert {u["email"] for u in users.json()["users"]} == {"owner@acme.test", "member@acme.test"}


async def test_owner_safety_guards(ctx):
    owner = await signup_owner(ctx.client)
    # Nobody can touch their own role, even the sole owner.
    assert (
        await ctx.client.put(f"/auth/users/{owner['user_id']}/role", json={"role": "member"})
    ).status_code == 400
    assert (await ctx.client.delete(f"/auth/users/{owner['user_id']}")).status_code == 400

    # With a second owner present, demoting one of them works...
    invite = await ctx.client.post(
        "/auth/invite", json={"email": "owner2@acme.test", "role": "owner"}
    )
    owner2_id = None
    async with fresh_client(ctx.app) as second:
        accepted = await second.post(
            "/auth/accept", json={"token": invite.json()["token"], "password": "correct-horse-5"}
        )
        assert accepted.status_code == 201
        owner2_id = accepted.json()["user"]["user_id"]
    demote = await ctx.client.put(f"/auth/users/{owner2_id}/role", json={"role": "admin"})
    assert demote.status_code == 200
    # ...and the demoted admin can no longer touch roles.
    async with fresh_client(ctx.app) as second:
        await second.post(
            "/auth/login", json={"email": "owner2@acme.test", "password": "correct-horse-5"}
        )
        assert (
            await second.put(f"/auth/users/{owner['user_id']}/role", json={"role": "viewer"})
        ).status_code == 403


async def test_unauthenticated_requests_are_rejected(ctx):
    # Regression guard: every router requires a principal. Spot-check one
    # read, one write and the destructive reset without any credentials.
    await signup_owner(ctx.client)
    async with fresh_client(ctx.app) as anon:
        assert (await anon.get("/templates")).status_code == 401
        assert (await anon.get("/auth/me")).status_code == 401
        assert (await anon.post("/organization/reset")).status_code == 401
        assert (
            await anon.post("/calls/simulate", json={"agent_id": "a", "to_number": "+91"})
        ).status_code == 401


async def test_logout_kills_session(ctx):
    await signup_owner(ctx.client)
    assert (await ctx.client.get("/auth/me")).status_code == 200
    assert (await ctx.client.post("/auth/logout")).status_code == 200
    assert (await ctx.client.get("/auth/me")).status_code == 401
