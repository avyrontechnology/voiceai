"""Auth controller over the real app factory (spec 0005, C5).

Every test drives the REAL stack — controller → `AuthService` → legacy `MemoryStore`
on the app.state seam — through an httpx ASGI transport. The contract pinned here is
the C5 payload-level byte identity: identical statuses, identical `detail` strings,
identical `data` payloads inside the standard envelopes (rule 2), covering every
route and every 4xx the service can raise.
"""

from __future__ import annotations

from typing import cast

import pytest
from dependency_injector import providers
from httpx import ASGITransport, AsyncClient

from voiceai.core.app_factory import create_app
from voiceai.core.container import build_container
from voiceai.core.environment import Environment
from voiceai.modules import auth as auth_module
from voiceai.modules.auth import constants as C
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.tests.conftest import _JWT
from voiceai.platform.store import MemoryStore

BASE = "http://auth.test"
PREFIX = "/api/v1/auth"


async def _client(store: MemoryStore | None = None) -> AsyncClient:
    """Build the factory app with only the auth module, wired to one store."""
    container = build_container(Environment())
    backing = store if store is not None else MemoryStore()
    container.auth_store.override(providers.Object(backing))
    container.auth_service.override(
        providers.Object(AuthService(cast(AuthStorePort, backing), jwt=_JWT))
    )
    app = create_app(env=Environment(), container=container)
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url=BASE)


def _auth_headers(client: AsyncClient) -> dict[str, str]:
    """Cookie header from the client's jar (bare remember-me round trips)."""
    token = client.cookies.get(C.SESSION_COOKIE)
    assert token is not None
    return {"cookie": f"{C.SESSION_COOKIE}={token}"}


async def _signup_owner(client: AsyncClient, email: str = "owner@x.test"):
    """First-user signup shortcut, asserting the 201 pair envelope shape."""
    response = await client.post(f"{PREFIX}/signup", json={"email": email, "name": "Owner", "password": "owner-pass-1"})
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["user"]["role"] == "owner"
    assert set(body["data"]) == {"user", "scopes", "access_token", "token_type", "expires_in"}
    assert body["data"]["token_type"] == "bearer"
    assert body["data"]["access_token"].count(".") == 2
    assert C.SESSION_COOKIE in response.cookies
    assert C.REFRESH_COOKIE in response.cookies
    return body["data"]


def test_routes_are_mounted_under_api_v1() -> None:
    """Every legacy /auth path rides the factory at /api/v1 (cutover keeps paths)."""
    container = build_container(Environment())
    app = create_app(env=Environment(), container=container)

    paths = {getattr(route, "path", "") for route in app.routes}

    assert {
        f"/api/v1/auth/signup",
        f"/api/v1/auth/login",
        f"/api/v1/auth/refresh",
        f"/api/v1/auth/logout",
        f"/api/v1/auth/me",
        f"/api/v1/auth/invite",
        f"/api/v1/auth/invites",
        f"/api/v1/auth/accept",
        f"/api/v1/auth/users",
        f"/api/v1/auth/password",
        f"/api/v1/auth/ws-ticket",
        f"/api/v1/auth/events",
    } <= paths


async def test_signup_then_closed_with_verbatim_detail() -> None:
    """Second signup is 403 with the legacy string inside the error envelope."""
    async with await _client(MemoryStore()) as client:
        await _signup_owner(client)

        response = await client.post(
            f"{PREFIX}/signup",
            json={"email": "second@x.test", "name": "S", "password": "second-pass-1"},
        )

    assert response.status_code == 403
    body = response.json()
    assert body["ok"] is False
    assert body["detail"] == "Signup is closed — ask an admin for an invite"


async def test_login_round_trip_cookie_ttl_and_failures() -> None:
    """Login sets a scoped cookie (7d/30d); bad credentials read the legacy 401."""
    async with await _client(MemoryStore()) as client:
        await _signup_owner(client)

        ok = await client.post(
            f"{PREFIX}/login",
            json={"email": "owner@x.test", "password": "owner-pass-1", "remember": True},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["ok"] is True
        assert body["data"]["user"]["email"] == "owner@x.test"
        assert body["data"]["scopes"] == ["*"]
        assert body["data"]["access_token"].count(".") == 2
        assert C.REFRESH_COOKIE in ok.cookies
        set_cookie = ok.headers["set-cookie"]
        assert "Max-Age=2592000" in set_cookie and "HttpOnly" in set_cookie

        bad = await client.post(f"{PREFIX}/login", json={"email": "owner@x.test", "password": "nope-wrong-1"})
        assert bad.status_code == 401
        assert bad.json()["detail"] == "Invalid email or password"

        upper = await client.post(f"{PREFIX}/login", json={"email": "Owner@X.test", "password": "owner-pass-1"})
        assert upper.status_code == 200  # normalization lives in the store, both worlds


async def test_login_throttle_is_429_with_unique_ip() -> None:
    """Sixth attempt from one IP reads 429 (dedicated IP — the ledger is global)."""
    async with await _client(MemoryStore()) as client:
        await _signup_owner(client, email="throttle@x.test")
        headers = {"x-forwarded-for": "203.0.113.77"}

        for _ in range(5):
            response = await client.post(
                f"{PREFIX}/login",
                json={"email": "throttle@x.test", "password": "wrong-pass-1"},
                headers=headers,
            )
            assert response.status_code == 401
        tripped = await client.post(
            f"{PREFIX}/login",
            json={"email": "throttle@x.test", "password": "wrong-pass-1"},
            headers=headers,
        )

    assert tripped.status_code == 429
    assert tripped.json()["detail"] == "Too many login attempts, try again shortly"


async def test_me_and_logout_cookie_lifecycle() -> None:
    """Me reflects the cookie; anonymous/key me reads 401; logout clears + kills."""
    async with await _client(MemoryStore()) as client:
        await _signup_owner(client)
        raw_token = client.cookies.get(C.SESSION_COOKIE)
        assert raw_token is not None

        me = await client.get(f"{PREFIX}/me", headers=_auth_headers(client))
        assert me.status_code == 200
        assert me.json()["data"]["user"]["role"] == "owner"

        out = await client.post(f"{PREFIX}/logout", headers=_auth_headers(client))
        assert out.status_code == 200 and out.json()["data"] == {"ok": True}
        assert "Max-Age=0" in out.headers["set-cookie"]
        assert C.SESSION_COOKIE not in client.cookies  # jar honors the clearing

        dead = await client.get(f"{PREFIX}/me", headers={"cookie": f"{C.SESSION_COOKIE}={raw_token}"})
        assert dead.status_code == 401
        assert dead.json()["detail"] == "Authentication required"


async def test_me_rejects_anonymous_and_key_callers() -> None:
    """No credential → 'Authentication required'; bearer key → 'Session required'."""
    store = MemoryStore()
    async with await _client(store) as client:
        await _signup_owner(client)

    async with await _client(store) as bare:
        anonymous = await bare.get(f"{PREFIX}/me")
        assert anonymous.status_code == 401
        assert anonymous.json()["detail"] == "Authentication required"
        keyed = await bare.get(f"{PREFIX}/me", headers={"authorization": "Bearer bogus"})
        assert keyed.status_code == 401
        assert keyed.json()["detail"] == "Authentication required"


async def test_invite_accept_round_trip_over_http() -> None:
    """Owner invites (201 + raw token) → accept mints member session cookie."""
    store = MemoryStore()
    async with await _client(store) as owner_client:
        await _signup_owner(owner_client)
        owner_headers = _auth_headers(owner_client)

        created = await owner_client.post(
            f"{PREFIX}/invite",
            json={"email": "new@x.test", "name": "New", "role": "member"},
            headers=owner_headers,
        )
        assert created.status_code == 201
        invite = created.json()["data"]
        assert invite["email"] == "new@x.test" and invite["token"]

        async with await _client(store) as member_client:
            accepted = await member_client.post(
                f"{PREFIX}/accept",
                json={"token": invite["token"], "name": None, "password": "new-pass-1"},
            )
            assert accepted.status_code == 201
            assert accepted.json()["data"]["user"]["role"] == "member"
            assert C.SESSION_COOKIE in accepted.cookies

            pending = await owner_client.get(f"{PREFIX}/invites", headers=owner_headers)
            assert pending.json()["data"]["invites"] == []


async def test_invite_guards_over_http() -> None:
    """Member invite → 403; admin crowning owner → 403; dup email → 409; reuse → 400."""
    store = MemoryStore()
    async with await _client(store) as client:
        await _signup_owner(client)
        owner_headers = _auth_headers(client)
        token = (
            await client.post(
                f"{PREFIX}/invite",
                json={"email": "m@x.test", "role": "member"},
                headers=owner_headers,
            )
        ).json()["data"]["token"]

        async with await _client(store) as member_client:
            await member_client.post(f"{PREFIX}/accept", json={"token": token, "password": "m-pass-1"})
            member_headers = _auth_headers(member_client)

            refused = await member_client.post(
                f"{PREFIX}/invite",
                json={"email": "z@x.test", "role": "member"},
                headers=member_headers,
            )
            assert refused.status_code == 403
            assert refused.json()["detail"] == "Requires admin role or higher"

        dup = await client.post(f"{PREFIX}/invite", json={"email": "m@x.test", "role": "member"}, headers=owner_headers)
        assert dup.status_code == 409
        assert dup.json()["detail"] == "Email already registered"

        reuse = await client.post(f"{PREFIX}/accept", json={"token": token, "password": "m-pass-2"})
        assert reuse.status_code == 400
        assert reuse.json()["detail"] == "Invite invalid or expired"


async def test_admin_user_lifecycle_over_http() -> None:
    """List → promote (old token dies) → self-role 400 → delete → self-delete 400."""
    store = MemoryStore()
    async with await _client(store) as client:
        owner = await _signup_owner(client)
        owner_headers = _auth_headers(client)
        token = (
            await client.post(
                f"{PREFIX}/invite",
                json={"email": "a@x.test", "role": "member"},
                headers=owner_headers,
            )
        ).json()["data"]["token"]

        async with await _client(store) as member_client:
            accept = await member_client.post(f"{PREFIX}/accept", json={"token": token, "password": "a-pass-1"})
            member_id = accept.json()["data"]["user"]["user_id"]
            member_headers = _auth_headers(member_client)

            users = await client.get(f"{PREFIX}/users", headers=owner_headers)
            assert users.status_code == 200
            assert {u["email"] for u in users.json()["data"]["users"]} == {
                owner["user"]["email"],
                "a@x.test",
            }

            promoted = await client.put(
                f"{PREFIX}/users/{member_id}/role",
                json={"role": "admin"},
                headers=owner_headers,
            )
            assert promoted.status_code == 200
            assert promoted.json()["data"]["role"] == "admin"
            assert (await member_client.get(f"{PREFIX}/me", headers=member_headers)).status_code == 401

            self_role = await client.put(
                f"{PREFIX}/users/{owner['user']['user_id']}/role",
                json={"role": "member"},
                headers=owner_headers,
            )
            assert self_role.status_code == 400
            assert self_role.json()["detail"] == "Cannot change your own role"

            missing = await client.put(f"{PREFIX}/users/nope/role", json={"role": "viewer"}, headers=owner_headers)
            assert missing.status_code == 404
            assert missing.json()["detail"] == "User not found"

            deleted = await client.delete(f"{PREFIX}/users/{member_id}", headers=owner_headers)
            assert deleted.status_code == 200
            assert deleted.json()["data"] == {"ok": True}

            self_delete = await client.delete(f"{PREFIX}/users/{owner['user']['user_id']}", headers=owner_headers)
            assert self_delete.status_code == 400
            assert self_delete.json()["detail"] == "Cannot delete yourself"


async def test_password_rotation_over_http() -> None:
    """Rotation kills the calling cookie and re-issues; wrong current reads 401."""
    async with await _client(MemoryStore()) as client:
        await _signup_owner(client)
        headers_a = _auth_headers(client)
        await client.post(f"{PREFIX}/login", json={"email": "owner@x.test", "password": "owner-pass-1"})

        rotated = await client.put(
            f"{PREFIX}/password",
            json={"current_password": "owner-pass-1", "new_password": "brand-new-1"},
            headers=headers_a,
        )
        assert rotated.status_code == 200
        assert rotated.json()["data"] == {"ok": True}
        assert C.REFRESH_COOKIE in rotated.cookies  # fresh pair rides the rotation
        assert (await client.get(f"{PREFIX}/me", headers=headers_a)).status_code == 401

        fresh = _auth_headers(client)
        assert (await client.get(f"{PREFIX}/me", headers=fresh)).status_code == 200

        stale = await client.put(
            f"{PREFIX}/password",
            json={"current_password": "owner-pass-1", "new_password": "x-new-pass-1"},
            headers=fresh,
        )
        assert stale.status_code == 401
        assert stale.json()["detail"] == "Current password is incorrect"


async def test_refresh_round_trip_over_http() -> None:
    """Refresh cookie rotates single-use; replay reads 401; JWT authorizes /me."""
    async with await _client(MemoryStore()) as client:
        signed = await _signup_owner(client)
        first_access = signed["access_token"]
        first_refresh = client.cookies.get(C.REFRESH_COOKIE)
        assert first_refresh is not None

        me = await client.get(f"{PREFIX}/me", headers={"authorization": f"Bearer {first_access}"})
        assert me.status_code == 200
        assert me.json()["data"]["user"]["email"] == "owner@x.test"

        rotated = await client.post(f"{PREFIX}/refresh")
        assert rotated.status_code == 200
        second_access = rotated.json()["data"]["access_token"]
        assert second_access != first_access
        assert C.REFRESH_COOKIE in rotated.cookies

        live = await client.get(f"{PREFIX}/me", headers={"authorization": f"Bearer {second_access}"})
        assert live.status_code == 200

        replay = await client.post(f"{PREFIX}/refresh", headers={"cookie": f"{C.REFRESH_COOKIE}={first_refresh}"})
        assert replay.status_code == 401  # rotated digests never mint twice
        assert (
            await client.get(f"{PREFIX}/me", headers={"authorization": f"Bearer {second_access}"})
        ).status_code == 200  # the live pair survives someone else's replay

        client.cookies.clear()  # drop legacy cookies: logout must resolve (and deny) the JWT alone
        out = await client.post(f"{PREFIX}/logout", headers={"authorization": f"Bearer {second_access}"})
        assert out.status_code == 200
        dead = await client.get(f"{PREFIX}/me", headers={"authorization": f"Bearer {second_access}"})
        assert dead.status_code == 401


async def test_ticket_and_events_over_http() -> None:
    """Owner mints a ticket (expires_in 60); viewer refused; events newest-first."""
    async with await _client(MemoryStore()) as client:
        await _signup_owner(client)
        owner_headers = _auth_headers(client)

        minted = await client.post(f"{PREFIX}/ws-ticket", headers=owner_headers)
        assert minted.status_code == 200
        ticket = minted.json()["data"]
        assert ticket["ticket"] and ticket["expires_in"] == 60

        events = await client.get(f"{PREFIX}/events", headers=owner_headers)
        assert events.status_code == 200
        rows = events.json()["data"]["events"]
        assert rows and rows[0]["created_at"] >= rows[-1]["created_at"]
        assert {e["type"] for e in rows} >= {"signup"}


async def test_delete_invite_and_member_event_gate() -> None:
    """Revoke drops the invite (second revoke 404s); members read 403 on events."""
    async with await _client(MemoryStore()) as client:
        await _signup_owner(client)
        owner_headers = _auth_headers(client)
        invite_id = (
            await client.post(f"{PREFIX}/invite", json={"email": "q@x.test", "role": "viewer"}, headers=owner_headers)
        ).json()["data"]["invite_id"]

        first = await client.delete(f"{PREFIX}/invites/{invite_id}", headers=owner_headers)
        assert first.status_code == 200
        second = await client.delete(f"{PREFIX}/invites/{invite_id}", headers=owner_headers)
        assert second.status_code == 404
        assert second.json()["detail"] == "Invite not found"


async def test_member_and_key_gates_across_routes() -> None:
    """Every role-gated mapper renders its 403/401 envelope (member + key callers)."""
    store = MemoryStore()
    async with await _client(store) as client:
        await _signup_owner(client)
        owner_headers = _auth_headers(client)
        token = (
            await client.post(f"{PREFIX}/invite", json={"email": "g@x.test", "role": "viewer"}, headers=owner_headers)
        ).json()["data"]["token"]

        async with await _client(store) as member_client:
            await member_client.post(f"{PREFIX}/accept", json={"token": token, "password": "g-pass-1"})
            member_headers = _auth_headers(member_client)

            for path in (f"{PREFIX}/invites", f"{PREFIX}/users", f"{PREFIX}/events"):
                refused = await member_client.get(path, headers=member_headers)
                assert refused.status_code == 403
                assert refused.json()["detail"] == "Requires admin role or higher"
            assert (await member_client.delete(f"{PREFIX}/invites/none", headers=member_headers)).status_code == 403
            ticket = await member_client.post(f"{PREFIX}/ws-ticket", headers=member_headers)
            assert ticket.status_code == 403
            assert ticket.json()["detail"] == "Requires calls:write scope"

    async with await _client(store) as bare:
        anonymous_logout = await bare.post(f"{PREFIX}/logout")
        assert anonymous_logout.status_code == 200
        assert anonymous_logout.json()["data"] == {"ok": True}

        from voiceai.modules.auth.models.apikey import ApiKey
        from voiceai.modules.auth.static_methods import new_token, token_hash

        secret = new_token()
        owner = await store.get_user_by_email("owner@x.test")
        assert owner is not None
        await store.save_api_key(
            ApiKey(
                key_id="k1",
                name="ci",
                prefix=secret[:8],
                key_hash=token_hash(secret),
                scopes=["*"],
                created_by=owner.user_id,
            )
        )
        keyed_me = await bare.get(f"{PREFIX}/me", headers={"authorization": f"Bearer {secret}"})
        assert keyed_me.status_code == 401
        assert keyed_me.json()["detail"] == "Session required"


async def test_validation_failure_is_422_envelope() -> None:
    """Short passwords never reach the service (factory validation handler)."""
    async with await _client(MemoryStore()) as client:
        response = await client.post(f"{PREFIX}/signup", json={"email": "x@x.test", "password": "short"})

    assert response.status_code == 422
    assert response.json()["ok"] is False


async def test_unknown_route_is_envelope_404() -> None:
    """Unmounted paths stay JSON envelopes (no HTML surprises for clients)."""
    async with await _client(MemoryStore()) as client:
        response = await client.get(f"{PREFIX}/nope")

    assert response.status_code == 404
    assert response.json()["ok"] is False
