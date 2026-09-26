"""Auth tenant context: resolvers stamp tenant+teams, me() gates on tenant (spec 0040, Agent B).

Resolvers also treat suspended tenants like disabled users (no principal).
Self-contained by design: the minimal local fake implements only the store
seam these tests exercise (`get_user`/`get_session`/`get_api_key_by_hash`/
`save_api_key`/`is_revoked`/`get_tenant`/`list_memberships`) with local
membership/tenant rows — Agent A's `FakeAuthStore` is deliberately not
imported, so this file passes with or without its extensions.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import cast

import pytest

from voiceai.common.datetime_utils import utc_now
from voiceai.modules.auth.errors import AuthNotFoundError, InvalidCredentialsError
from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.tenant import Tenant
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.static_methods import issue_access_token, new_token, token_hash
from voiceai.modules.auth.tests.conftest import _JWT

#: Tenant object hexes carried through these tests (opaque strings — the
#: service never parses them, it only compares).
TENANT_HEX = "64f000000000000000000001"
OTHER_TENANT_HEX = "64f000000000000000000002"
#: Pre-identity fallback the resolvers stamp when a row carries no tenant.
DEFAULT_TENANT = "default"
#: Team ids backing the membership fixtures.
TEAM_ONE = "team_000000000001"
TEAM_TWO = "team_000000000002"


@dataclass
class _Membership:
    """Local stand-in for Agent A's `Membership` (only the fields resolvers project)."""

    team_id: str
    role: str


class _TenantFakeStore:
    """Minimal dict-backed store seam for the tenant-context tests.

    Implements only what these tests exercise; fixtures write rows straight
    into the dicts. Cast to `AuthStorePort` at the service boundary.
    """

    def __init__(self) -> None:
        self.users: dict[str, User] = {}
        self.sessions: dict[str, SessionRecord] = {}
        self.keys: dict[str, ApiKey] = {}
        self.tenants: dict[str, Tenant] = {}
        self.memberships: dict[str, list[_Membership]] = {}

    async def get_user(self, user_id: str) -> User | None:
        """Return the user row, or `None`."""
        return self.users.get(user_id)

    async def get_session(self, token_hash_: str) -> SessionRecord | None:
        """Return the session row, or `None`."""
        return self.sessions.get(token_hash_)

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        """Return the key with this secret hash, or `None`."""
        return next((key for key in self.keys.values() if key.key_hash == key_hash), None)

    async def save_api_key(self, key: ApiKey) -> None:
        """Persist a key (the resolver touches `last_used_at`)."""
        self.keys[key.key_id] = key

    async def is_revoked(self, jti: str) -> bool:
        """No denylist rows exist in these tests (`jti` is accepted and ignored)."""
        _ = jti
        return False

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        """Return the tenant row, or `None`."""
        return self.tenants.get(tenant_id)

    async def list_memberships(self, user_id: str) -> list[_Membership]:
        """Return the user's membership rows (empty when there are none)."""
        return list(self.memberships.get(user_id, []))


def _service(store: _TenantFakeStore) -> AuthService:
    """Build the real service over the local fake (cast: the fake covers the exercised seam)."""
    return AuthService(cast(AuthStorePort, store), jwt=_JWT)


def _tenant_user(tenant_id: str | None = TENANT_HEX) -> User:
    """One member row in org `acme`, stamped (or pre-identity when `None`)."""
    return User(
        user_id="usr-tenant",
        email="tenant@x.test",
        name="Tenant",
        password_hash="x",
        role="member",
        org_id="acme",
        tenant_id=tenant_id,
    )


def _with_memberships(store: _TenantFakeStore, user_id: str) -> None:
    """Seed two memberships: admin on team one, member on team two."""
    store.memberships[user_id] = [
        _Membership(team_id=TEAM_ONE, role="admin"),
        _Membership(team_id=TEAM_TWO, role="member"),
    ]


def _session_for(store: _TenantFakeStore, user: User) -> str:
    """Seed one live session row for a user, returning the raw token."""
    raw = new_token()
    store.sessions[token_hash(raw)] = SessionRecord(
        token_hash=token_hash(raw),
        user_id=user.user_id,
        org_id=user.org_id,
        kind="session",
        expires_at=utc_now() + timedelta(seconds=3600),
    )
    return raw


def test_principal_tenant_defaults() -> None:
    """Hand-built principals keep the system tenant and no teams (spec 0040)."""
    principal = Principal(user_id=None, email=None)

    assert principal.tenant_id == "system"
    assert principal.teams == []
    assert principal.team_roles == {}


async def test_session_resolver_populates_tenant_and_teams() -> None:
    """A session login resolves tenant hex plus team ids and roles."""
    store = _TenantFakeStore()
    user = _tenant_user()
    store.users[user.user_id] = user
    _with_memberships(store, user.user_id)
    raw = _session_for(store, user)

    principal = await _service(store).authenticate(raw, None)

    assert principal.tenant_id == TENANT_HEX
    assert principal.teams == [TEAM_ONE, TEAM_TWO]
    assert principal.team_roles == {TEAM_ONE: "admin", TEAM_TWO: "member"}
    assert (principal.org_id, principal.role) == ("acme", "member")


async def test_api_key_resolver_populates_tenant_and_teams() -> None:
    """A bearer API key resolves tenant hex plus team ids and roles."""
    store = _TenantFakeStore()
    user = _tenant_user()
    store.users[user.user_id] = user
    _with_memberships(store, user.user_id)
    secret = new_token()
    store.keys["key-1"] = ApiKey(
        key_id="key-1",
        name="tenant-key",
        prefix="ob_tenant",
        key_hash=token_hash(secret),
        scopes=["agents:read"],
        created_by=user.user_id,
    )

    principal = await _service(store).authenticate(None, f"Bearer {secret}")

    assert principal.tenant_id == TENANT_HEX
    assert principal.teams == [TEAM_ONE, TEAM_TWO]
    assert principal.team_roles == {TEAM_ONE: "admin", TEAM_TWO: "member"}
    assert principal.auth_type == "key"


async def test_jwt_resolver_populates_tenant_and_teams() -> None:
    """A bearer JWT resolves tenant hex plus team ids and roles."""
    store = _TenantFakeStore()
    user = _tenant_user()
    store.users[user.user_id] = user
    _with_memberships(store, user.user_id)
    token = issue_access_token(
        user_id=user.user_id,
        org_id=user.org_id,
        role=user.role,
        token_version=0,
        jti=new_token(),
        issuer=_JWT.issuer,
        audience=_JWT.audience,
        access_ttl_s=900,
        private_key=_JWT.private_key,
        now=utc_now(),
    )

    principal = await _service(store).authenticate(None, f"Bearer {token}")

    assert principal.tenant_id == TENANT_HEX
    assert principal.teams == [TEAM_ONE, TEAM_TWO]
    assert principal.team_roles == {TEAM_ONE: "admin", TEAM_TWO: "member"}
    assert principal.token_id is not None


async def test_resolvers_fall_back_without_tenant_or_memberships() -> None:
    """A pre-identity row resolves to the default tenant with no teams."""
    store = _TenantFakeStore()
    user = _tenant_user(tenant_id=None)
    store.users[user.user_id] = user
    raw = _session_for(store, user)

    principal = await _service(store).authenticate(raw, None)

    assert principal.tenant_id == DEFAULT_TENANT
    assert principal.teams == []
    assert principal.team_roles == {}


async def test_suspended_tenant_resolves_no_principal() -> None:
    """A user whose tenant row is suspended authenticates like a disabled user."""
    store = _TenantFakeStore()
    user = _tenant_user()
    store.users[user.user_id] = user
    store.tenants[TENANT_HEX] = Tenant(tenant_id=TENANT_HEX, slug="acme", name="Acme", status="suspended")
    raw = _session_for(store, user)

    with pytest.raises(InvalidCredentialsError, match="Authentication required"):
        await _service(store).authenticate(raw, None)


async def test_active_tenant_still_resolves() -> None:
    """A user whose tenant row is active resolves with the tenant hex."""
    store = _TenantFakeStore()
    user = _tenant_user()
    store.users[user.user_id] = user
    store.tenants[TENANT_HEX] = Tenant(tenant_id=TENANT_HEX, slug="acme", name="Acme", status="active")
    _with_memberships(store, user.user_id)
    raw = _session_for(store, user)

    principal = await _service(store).authenticate(raw, None)

    assert principal.tenant_id == TENANT_HEX
    assert principal.teams == [TEAM_ONE, TEAM_TWO]


def test_model_tenant_no_longer_syncs_from_org() -> None:
    """Validator retirement: construction stamps nothing — tenant stays explicit-or-None."""
    user = User(user_id="u-1", email="u@x.test", password_hash="x", org_id="acme")

    assert user.tenant_id is None
    assert User.model_validate(user.model_dump()).tenant_id is None

    stamped = User(user_id="u-2", email="v@x.test", password_hash="x", org_id="acme", tenant_id=TENANT_HEX)

    assert stamped.tenant_id == TENANT_HEX
    assert User.model_validate(stamped.model_dump()).tenant_id == TENANT_HEX

    session = SessionRecord(
        token_hash="h", user_id="u-1", org_id="acme", expires_at=utc_now() + timedelta(seconds=60)
    )

    assert session.tenant_id is None

    invite = Invite(
        invite_id="inv-1",
        email="i@x.test",
        org_id="acme",
        token_hash="h",
        expires_at=utc_now() + timedelta(seconds=60),
    )

    assert invite.tenant_id is None


async def test_me_accepts_matching_tenant() -> None:
    """me() returns the record when the user tenant matches the principal tenant."""
    store = _TenantFakeStore()
    user = _tenant_user()
    store.users[user.user_id] = user
    principal = Principal(
        user_id=user.user_id,
        email=user.email,
        org_id="acme",
        tenant_id=TENANT_HEX,
        role="member",
        auth_type="session",
    )

    found, _ = await _service(store).me(principal)

    assert found.user_id == user.user_id


async def test_me_rejects_cross_tenant_as_missing() -> None:
    """me() reads a cross-tenant user exactly like a missing one (no oracle)."""
    store = _TenantFakeStore()
    user = _tenant_user()
    store.users[user.user_id] = user
    foreign = Principal(
        user_id=user.user_id,
        email=user.email,
        org_id="acme",
        tenant_id=OTHER_TENANT_HEX,
        role="member",
        auth_type="session",
    )

    with pytest.raises(AuthNotFoundError, match="User not found"):
        await _service(store).me(foreign)


async def test_me_pre_identity_row_keeps_org_boundary() -> None:
    """me() on an unstamped row falls back to the legacy org check until backfill."""
    store = _TenantFakeStore()
    user = _tenant_user(tenant_id=None)
    store.users[user.user_id] = user
    service = _service(store)
    same_org = Principal(user_id=user.user_id, email=user.email, org_id="acme", auth_type="session")
    other_org = Principal(user_id=user.user_id, email=user.email, org_id="globex", auth_type="session")

    found, _ = await service.me(same_org)

    assert found.user_id == user.user_id
    with pytest.raises(AuthNotFoundError, match="User not found"):
        await service.me(other_org)
