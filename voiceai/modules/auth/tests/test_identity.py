"""Identity program: tenant/org/team/membership CRUD units (spec 0040, Agent A).

Every test drives the REAL `AuthService` against `FakeAuthStore` (extended in
`test_service.py` by Agent A): conflicts (dup slug, dup membership), gates
(non-owner forbidden), fake-store round trips, and tenant scoping (a second
tenant's rows stay invisible). The Mongo store's pre-cutover dict fallback is
pinned here too (natural-key id pinning).
"""

from __future__ import annotations

import pytest
from bson import ObjectId

from voiceai.common.errors import ConflictError
from voiceai.core.db import InMemoryDatabase
from voiceai.database.constants import Collections
from voiceai.database.repository import InMemoryRepository
from voiceai.modules.auth.errors import AuthNotFoundError, ForbiddenError
from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.membership import Membership
from voiceai.modules.auth.models.organization import Organization
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.revoked import RevokedToken
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.team import Team
from voiceai.modules.auth.models.tenant import Tenant
from voiceai.modules.auth.models.user import User, UserRole
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.modules.auth.repository import MongoAuthStore
from voiceai.modules.auth.service import AuthService
from voiceai.modules.auth.tests.test_service import FakeAuthStore


def _principal(user_id: str, org_id: str, role: UserRole) -> Principal:
    """Act-as principal the way the session resolver builds one."""
    return Principal(
        user_id=user_id,
        email=f"{user_id}@x.test",
        org_id=org_id,
        role=role,
        auth_type="session",
    )


def _mongo_store() -> MongoAuthStore:
    """Real store over in-memory collections; identity repos fall back local."""
    database = InMemoryDatabase()
    return MongoAuthStore(
        users=InMemoryRepository(database, Collections.USERS, User),
        sessions=InMemoryRepository(database, Collections.SESSIONS, SessionRecord),
        invites=InMemoryRepository(database, Collections.INVITES, Invite),
        keys=InMemoryRepository(database, Collections.API_KEYS, ApiKey),
        events=InMemoryRepository(database, Collections.AUTH_EVENTS, AuthEvent),
        revoked=InMemoryRepository(database, Collections.REVOKED_TOKENS, RevokedToken),
    )


async def _tenant(service: AuthService, owner: Principal, slug: str = "default") -> Tenant:
    """Create-tenant shortcut (owner + slug + derived display name)."""
    return await service.create_tenant(owner, slug, f"{slug} Inc")


async def _tenant_stack(service: AuthService, owner: Principal, slug: str) -> tuple[Tenant, str, str]:
    """Tenant → organization → team ids, all under one slug."""
    tenant = await _tenant(service, owner, slug)
    scoped = _principal(owner.user_id or "owner", tenant.tenant_id, "owner")
    org = await service.create_organization(scoped, tenant.tenant_id, f"{slug} Org")
    team = await service.create_team(scoped, org.org_id, f"{slug} Team")
    return tenant, org.org_id, team.team_id


# -- tenants ---------------------------------------------------------------


async def test_create_tenant_mints_objectid_hex_and_normalizes_slug() -> None:
    """New tenants carry an ObjectId hex key; slugs strip and lowercase."""
    service = AuthService(FakeAuthStore())

    tenant = await _tenant(service, _principal("owner-1", "default", "owner"), "Acme")

    assert str(ObjectId(tenant.tenant_id)) == tenant.tenant_id
    assert tenant.slug == "acme"
    assert tenant.plan == "default"
    assert tenant.status == "active"


async def test_create_tenant_duplicate_slug_conflicts() -> None:
    """A taken slug reads 409, even with different casing (verbatim order)."""
    service = AuthService(FakeAuthStore())
    owner = _principal("owner-1", "default", "owner")
    await _tenant(service, owner, "default")

    with pytest.raises(ConflictError, match="slug already registered"):
        await _tenant(service, owner, "Default")


async def test_create_tenant_requires_owner() -> None:
    """Admins and members read 403 on tenant creation (owner-only)."""
    service = AuthService(FakeAuthStore())

    with pytest.raises(ForbiddenError, match="Requires owner"):
        await _tenant(service, _principal("admin-1", "default", "admin"))
    with pytest.raises(ForbiddenError, match="Requires owner"):
        await _tenant(service, _principal("member-1", "default", "member"))


# -- organizations / teams / memberships ------------------------------------


async def test_organization_team_membership_round_trip() -> None:
    """Owner builds org → team; admin adds a member; the member reads it back."""
    store = FakeAuthStore()
    service = AuthService(store)
    owner = _principal("owner-1", "default", "owner")
    tenant, org_id, team_id = await _tenant_stack(service, owner, "acme")

    membership = await service.add_membership(
        _principal("owner-1", tenant.tenant_id, "admin"), team_id, "user-1", "member"
    )

    assert membership.team_id == team_id and membership.role == "member"
    assert membership.org_id == org_id and membership.tenant_id == tenant.tenant_id
    assert membership.membership_id.startswith("mem_")
    mine = await service.list_user_teams(_principal("user-1", tenant.tenant_id, "viewer"), "user-1")
    assert [t.team_id for t in mine] == [team_id]


async def test_add_membership_duplicate_conflicts() -> None:
    """A second add of the same (user, team) reads 409."""
    service = AuthService(FakeAuthStore())
    owner = _principal("owner-1", "default", "owner")
    tenant, _, team_id = await _tenant_stack(service, owner, "acme")
    admin = _principal("owner-1", tenant.tenant_id, "admin")
    await service.add_membership(admin, team_id, "user-1", "member")

    with pytest.raises(ConflictError, match="already a member"):
        await service.add_membership(admin, team_id, "user-1", "viewer")


async def test_identity_gates_reject_non_admins() -> None:
    """Members read 403 on team/membership writes and on other users' teams."""
    service = AuthService(FakeAuthStore())
    owner = _principal("owner-1", "default", "owner")
    tenant, org_id, team_id = await _tenant_stack(service, owner, "acme")
    member = _principal("user-1", tenant.tenant_id, "member")

    with pytest.raises(ForbiddenError, match="Requires admin"):
        await service.create_team(member, org_id, "sneaky")
    with pytest.raises(ForbiddenError, match="Requires admin"):
        await service.add_membership(member, team_id, "user-2", "member")
    with pytest.raises(ForbiddenError, match="Requires admin"):
        await service.remove_membership(member, team_id, "user-2")
    with pytest.raises(ForbiddenError, match="Requires admin"):
        await service.list_user_teams(member, "user-2")


async def test_remove_membership_round_trip_and_missing() -> None:
    """Remove drops the row; a second remove reads 404."""
    service = AuthService(FakeAuthStore())
    owner = _principal("owner-1", "default", "owner")
    tenant, _, team_id = await _tenant_stack(service, owner, "acme")
    admin = _principal("owner-1", tenant.tenant_id, "admin")
    await service.add_membership(admin, team_id, "user-1", "member")

    await service.remove_membership(admin, team_id, "user-1")

    assert await service.list_user_teams(admin, "user-1") == []
    with pytest.raises(AuthNotFoundError, match="Membership not found"):
        await service.remove_membership(admin, team_id, "user-1")


async def test_create_guards_unknown_parents() -> None:
    """Organizations need a tenant; teams and memberships need their parents."""
    service = AuthService(FakeAuthStore())
    owner = _principal("owner-1", "default", "owner")

    with pytest.raises(AuthNotFoundError, match="Tenant not found"):
        await service.create_organization(owner, "deadbeef" * 3, "ghost")
    with pytest.raises(AuthNotFoundError, match="Organization not found"):
        await service.create_team(owner, "org_nope", "ghost")
    with pytest.raises(AuthNotFoundError, match="Team not found"):
        await service.add_membership(owner, "team_nope", "user-1", "member")


# -- tenant scoping ----------------------------------------------------------


async def test_second_tenant_is_invisible() -> None:
    """Membership reads scope to the acting tenant — no cross-tenant oracle."""
    service = AuthService(FakeAuthStore())
    owner = _principal("owner-1", "default", "owner")
    tenant_a, _, team_a = await _tenant_stack(service, owner, "acme")
    tenant_b, _, _ = await _tenant_stack(service, owner, "globex")
    admin_a = _principal("owner-1", tenant_a.tenant_id, "admin")
    admin_b = _principal("owner-1", tenant_b.tenant_id, "admin")
    await service.add_membership(admin_a, team_a, "user-1", "member")

    assert await service.list_user_teams(admin_b, "user-1") == []
    with pytest.raises(AuthNotFoundError, match="Team not found"):
        await service.add_membership(admin_b, team_a, "user-2", "member")
    with pytest.raises(AuthNotFoundError, match="Membership not found"):
        await service.remove_membership(admin_b, team_a, "user-1")
    # The home tenant still reads its own row.
    assert [t.team_id for t in await service.list_user_teams(admin_a, "user-1")] == [team_a]


async def test_pre_migration_slug_principal_still_scopes() -> None:
    """A principal carrying the legacy slug resolves to the tenant hex server-side."""
    service = AuthService(FakeAuthStore())
    owner = _principal("owner-1", "default", "owner")
    tenant, _, team_id = await _tenant_stack(service, owner, "default")
    await service.add_membership(_principal("owner-1", tenant.tenant_id, "admin"), team_id, "user-1", "member")

    mine = await service.list_user_teams(_principal("user-1", "default", "viewer"), "user-1")

    assert [t.team_id for t in mine] == [team_id]


# -- store seams -------------------------------------------------------------


def test_fake_and_mongo_stores_satisfy_the_extended_port() -> None:
    """Both stores stand where the legacy stores stood, identity tail included."""
    assert isinstance(FakeAuthStore(), AuthStorePort)
    assert isinstance(_mongo_store(), AuthStorePort)


async def test_mongo_fallback_pins_natural_keys() -> None:
    """Pre-cutover rows pin `id` to the natural key and round-trip with filters."""
    store = _mongo_store()
    tenant = Tenant(tenant_id=str(ObjectId()), slug="acme", name="Acme")
    org = Organization(org_id="org_abc123def456", tenant_id=tenant.tenant_id, name="Acme Org")
    team = Team(
        team_id="team_abc123def456",
        org_id=org.org_id,
        tenant_id=tenant.tenant_id,
        name="Acme Team",
    )
    membership = Membership(
        membership_id="mem_abc123def456",
        user_id="user-1",
        team_id=team.team_id,
        org_id=org.org_id,
        tenant_id=tenant.tenant_id,
        role="member",
    )

    await store.save_tenant(tenant)
    await store.save_organization(org)
    await store.save_team(team)
    await store.save_membership(membership)

    assert tenant.id == tenant.tenant_id
    assert org.id == org.org_id
    assert team.id == team.team_id
    assert membership.id == membership.membership_id
    assert (await store.get_tenant(tenant.tenant_id)) is not None
    assert (await store.get_tenant_by_slug("acme")) is not None
    assert [t.slug for t in await store.list_tenants()] == ["acme"]
    assert [o.org_id for o in await store.list_organizations(tenant.tenant_id)] == [org.org_id]
    assert await store.list_organizations("nope") == []
    assert [t.team_id for t in await store.list_teams(org.org_id)] == [team.team_id]
    assert [m.membership_id for m in await store.list_memberships("user-1")] == [membership.membership_id]
    assert await store.delete_membership(membership.membership_id) is True
    assert await store.delete_membership(membership.membership_id) is False
    assert await store.list_memberships("user-1") == []


# -- ticket redemption (integrator seam) ---------------------------------------


async def test_redeemed_ticket_carries_projected_tenancy() -> None:
    """The ws ticket path projects tenant/teams like the other resolvers.

    `redeem_ticket` was the one credential path outside the three spec-owned
    resolvers; the voice WS controller binds `principal.tenant_id`, so a
    tenant-less ticket principal would strand the call on the system tenant.
    """
    store = FakeAuthStore()
    service = AuthService(store)
    owner = _principal("owner-1", "default", "owner")
    tenant, _, team_id = await _tenant_stack(service, owner, "acme")
    await service.add_membership(_principal("owner-1", tenant.tenant_id, "admin"), team_id, "user-1", "member")
    await store.save_user(
        User(
            user_id="user-1",
            email="user-1@x.test",
            password_hash="x",
            role="member",
            org_id="acme",
            tenant_id=tenant.tenant_id,
        )
    )

    ticket = await service.mint_ticket(_principal("user-1", tenant.tenant_id, "owner"))
    redeemed = await service.redeem_ticket(ticket)

    assert redeemed is not None
    assert redeemed.tenant_id == tenant.tenant_id
    assert redeemed.teams == [team_id]
    assert redeemed.team_roles == {team_id: "member"}
