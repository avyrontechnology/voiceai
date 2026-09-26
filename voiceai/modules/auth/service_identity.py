"""Auth identity slice: tenant/org/team/membership CRUD (spec 0040, Phase D).

`AuthIdentityMixin` owns the identity program: tenant, organization, and team
creation plus per-team membership grants, all scoped to the acting tenant with
no cross-tenant oracle. Natural keys pin `id` (auth `_pin` precedent); slugs
resolve server-side. It inherits the shared kernel
(`service_base.AuthServiceBase`) and composes into `service.AuthService` — one
responsibility per file. Split cited by spec 0040 (integrator): the identity
program grew `service.py` past the 800-line canonical budget.
"""

from __future__ import annotations

from bson import ObjectId

from voiceai.common.errors import ConflictError
from voiceai.common.ids import new_id
from voiceai.modules.auth import constants as C
from voiceai.modules.auth.errors import AuthNotFoundError
from voiceai.modules.auth.exceptions import ensure_authenticated, ensure_found, ensure_permitted
from voiceai.modules.auth.models.membership import Membership
from voiceai.modules.auth.models.organization import Organization
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.team import Team
from voiceai.modules.auth.models.tenant import Tenant
from voiceai.modules.auth.models.user import UserRole
from voiceai.modules.auth.service_base import AuthServiceBase

__all__ = ["AuthIdentityMixin"]


class AuthIdentityMixin(AuthServiceBase):
    """Identity-program CRUD over the shared kernel (mixed into `AuthService`)."""

    async def _acting_tenant_hex(self, principal: Principal) -> str:
        """Resolve the caller's tenant to its ObjectId hex for membership scoping.

        Slugs resolve server-side: a pre-migration `org_id` (`"default"`) maps
        to the tenant row's hex, so one comparison covers both credential eras.
        An unresolvable value scopes to itself — matching nothing rather than
        everything, so unknown tenants enumerate no rows.

        Args:
            principal: The acting caller.

        Returns:
            The acting tenant's hex.
        """
        direct = await self._store.get_tenant(principal.org_id)
        if direct is not None:
            return direct.tenant_id
        via_slug = await self._store.get_tenant_by_slug(principal.org_id)
        if via_slug is not None:
            return via_slug.tenant_id
        return principal.org_id

    async def create_tenant(
        self, principal: Principal, slug: str, name: str, plan: str = C.DEFAULT_PLAN
    ) -> Tenant:
        """Create a tenant; slugs are unique (owners only).

        Args:
            principal: The acting caller.
            slug: Human key (`"default"` for the migrated install); stripped
                and lowercased before the uniqueness check.
            name: Display name.
            plan: Billing plan tag.

        Raises:
            ForbiddenError: When the caller is not an owner.
            ConflictError: When the slug is taken.
        """
        await self._require_owner(principal)
        normalized = slug.strip().lower()
        if await self._store.get_tenant_by_slug(normalized) is not None:
            raise ConflictError("Tenant slug already registered")
        tenant = Tenant(
            tenant_id=str(ObjectId()),
            slug=normalized,
            name=name,
            plan=plan,
            created_by=principal.user_id,
        )
        await self._store.save_tenant(tenant)
        return tenant

    async def create_organization(self, principal: Principal, tenant_id: str, name: str) -> Organization:
        """Create an organization under a tenant (owners only).

        Args:
            principal: The acting caller.
            tenant_id: Owning tenant's ObjectId hex.
            name: Display name.

        Raises:
            ForbiddenError: When the caller is not an owner.
            AuthNotFoundError: When the tenant does not exist (no oracle —
                unknown tenants read exactly like missing ones).
        """
        await self._require_owner(principal)
        tenant: Tenant = ensure_found(await self._store.get_tenant(tenant_id), "Tenant not found")
        organization = Organization(
            org_id=new_id(C.ORG_ID_PREFIX),
            tenant_id=tenant.tenant_id,
            name=name,
            created_by=principal.user_id,
        )
        await self._store.save_organization(organization)
        return organization

    async def create_team(self, principal: Principal, org_id: str, name: str) -> Team:
        """Create a team under an organization, stamping its tenant (admins only).

        The tenant denormalizes from the organization row — callers never mint
        the boundary themselves (slug-to-hex stays server-side, spec 0040).

        Args:
            principal: The acting caller.
            org_id: Owning organization's natural key.
            name: Display name.

        Raises:
            ForbiddenError: When the caller is not an admin.
            AuthNotFoundError: When the organization does not exist.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        organization: Organization = ensure_found(
            await self._store.get_organization(org_id), "Organization not found"
        )
        team = Team(
            team_id=new_id(C.TEAM_ID_PREFIX),
            org_id=organization.org_id,
            tenant_id=organization.tenant_id,
            name=name,
            created_by=principal.user_id,
        )
        await self._store.save_team(team)
        return team

    async def add_membership(
        self, principal: Principal, team_id: str, user_id: str, role: UserRole
    ) -> Membership:
        """Add a user to a team with a per-team role (admins only).

        Args:
            principal: The acting caller.
            team_id: Target team's natural key.
            user_id: Subject user's id.
            role: Per-team grant.

        Raises:
            ForbiddenError: When the caller is not an admin.
            AuthNotFoundError: When the team does not exist or belongs to
                another tenant (no cross-tenant oracle).
            ConflictError: When the user is already on the team.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        team: Team = ensure_found(await self._store.get_team(team_id), "Team not found")
        if team.tenant_id != await self._acting_tenant_hex(principal):
            raise AuthNotFoundError("Team not found")
        for existing in await self._store.list_memberships(user_id):
            if existing.team_id == team_id:
                raise ConflictError("User is already a member of this team")
        membership = Membership(
            membership_id=new_id(C.MEMBERSHIP_ID_PREFIX),
            user_id=user_id,
            team_id=team.team_id,
            org_id=team.org_id,
            tenant_id=team.tenant_id,
            role=role,
            created_by=principal.user_id,
        )
        await self._store.save_membership(membership)
        return membership

    async def remove_membership(self, principal: Principal, team_id: str, user_id: str) -> None:
        """Remove a user from a team (admins only).

        Args:
            principal: The acting caller.
            team_id: Target team's natural key.
            user_id: Subject user's id.

        Raises:
            ForbiddenError: When the caller is not an admin.
            AuthNotFoundError: When the membership does not exist or belongs to
                another tenant (no cross-tenant oracle).
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        acting_hex = await self._acting_tenant_hex(principal)
        matched: Membership | None = None
        for membership in await self._store.list_memberships(user_id):
            if membership.team_id != team_id:
                continue
            if membership.tenant_id != acting_hex:
                raise AuthNotFoundError("Membership not found")
            matched = membership
            break
        target: Membership = ensure_found(matched, "Membership not found")
        await self._store.delete_membership(target.membership_id)

    async def list_user_teams(self, principal: Principal, user_id: str) -> list[Team]:
        """Return the teams a user belongs to, scoped to the acting tenant.

        Callers read their own teams; reading anyone else's needs admin.
        Cross-tenant memberships never surface (no enumeration oracle).

        Args:
            principal: The acting caller.
            user_id: Subject user's id.

        Raises:
            ForbiddenError: When the caller reads another user's teams without admin.
        """
        ensure_authenticated(principal)
        if principal.user_id != user_id:
            ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        acting_hex = await self._acting_tenant_hex(principal)
        teams: list[Team] = []
        for membership in await self._store.list_memberships(user_id):
            if membership.tenant_id != acting_hex:
                continue
            team = await self._store.get_team(membership.team_id)
            if team is not None:
                teams.append(team)
        return teams
