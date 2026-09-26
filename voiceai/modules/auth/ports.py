"""Store port the auth service depends on (AGENTS.md rule 9; T2 greenfield).

``AuthStorePort`` names exactly the store methods the auth domain touches. T2 adds
indexed lookups (`get_api_key_by_hash`, `get_invite_by_token_hash`) and the
revocation pair (`save_revoked`, `is_revoked`) so no flow scans a collection:
the Mongo store answers from indexes, the legacy stores from direct keys (with
two documented scans that die at the T7 cutover). Spec 0040 adds the identity
tail (`save_tenant` … `delete_membership`) for tenants, organizations, teams
and per-team memberships.
"""

from __future__ import annotations

from voiceai.modules.auth.models.apikey import ApiKey
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.membership import Membership
from voiceai.modules.auth.models.organization import Organization
from voiceai.modules.auth.models.revoked import RevokedToken
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.team import Team
from voiceai.modules.auth.models.tenant import Tenant
from voiceai.modules.auth.models.user import User

__all__ = ["AuthStorePort", "LoginLimiter"]

from typing import Protocol, runtime_checkable


@runtime_checkable
class AuthStorePort(Protocol):
    """Persistence the auth service needs — users, sessions, invites, keys, audit."""

    async def save_user(self, user: User) -> None:
        """Persist a user (insert or replace)."""
        ...

    async def get_user(self, user_id: str) -> User | None:
        """Return the user with this id, or `None`."""
        ...

    async def get_user_by_email(self, email: str) -> User | None:
        """Return the user with this email, or `None`."""
        ...

    async def list_users(self) -> list[User]:
        """Return every user."""
        ...

    async def count_users(self) -> int:
        """Return the number of users."""
        ...

    async def delete_user(self, user_id: str) -> bool:
        """Delete a user; `True` when one existed."""
        ...

    async def delete_user_sessions(self, user_id: str) -> int:
        """Delete every session of a user; return the count."""
        ...

    async def save_session(self, session: SessionRecord) -> None:
        """Persist a session record."""
        ...

    async def get_session(self, token_hash: str) -> SessionRecord | None:
        """Return the session for a token hash, or `None`."""
        ...

    async def delete_session(self, token_hash: str) -> bool:
        """Delete a session; `True` when one existed."""
        ...

    async def save_invite(self, invite: Invite) -> None:
        """Persist an invite."""
        ...

    async def get_invite(self, invite_id: str) -> Invite | None:
        """Return the invite with this id, or `None`."""
        ...

    async def get_invite_by_token_hash(self, token_hash: str) -> Invite | None:
        """Return the live invite with this token digest, or `None` (indexed, no scan)."""
        ...

    async def list_invites(self) -> list[Invite]:
        """Return every invite."""
        ...

    async def delete_invite(self, invite_id: str) -> bool:
        """Delete an invite; `True` when one existed."""
        ...

    async def list_api_keys(self) -> list[ApiKey]:
        """Return every API key."""
        ...

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        """Return the API key with this secret hash, or `None` (indexed, no scan)."""
        ...

    async def save_api_key(self, key: ApiKey) -> None:
        """Persist an API key."""
        ...

    async def add_auth_event(self, event: AuthEvent) -> None:
        """Append one audit event."""
        ...

    async def list_auth_events(self, limit: int = 100) -> list[AuthEvent]:
        """Return recent audit events, newest first."""
        ...

    async def list_user_sessions(self, user_id: str) -> list[SessionRecord]:
        """Return every session record of a user (password-change sweep)."""
        ...

    async def save_revoked(self, token: RevokedToken) -> None:
        """Deny one access token by its JWT id (single-logout path)."""
        ...

    async def is_revoked(self, jti: str) -> bool:
        """Report whether a JWT id was denied (cache-fast, store-backed)."""
        ...

    async def save_tenant(self, tenant: Tenant) -> None:
        """Persist a tenant (insert or replace)."""
        ...

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        """Return the tenant with this id, or `None`."""
        ...

    async def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        """Return the tenant with this slug, or `None` (indexed, no scan)."""
        ...

    async def list_tenants(self) -> list[Tenant]:
        """Return every tenant."""
        ...

    async def save_organization(self, organization: Organization) -> None:
        """Persist an organization (insert or replace)."""
        ...

    async def get_organization(self, org_id: str) -> Organization | None:
        """Return the organization with this id, or `None`."""
        ...

    async def list_organizations(self, tenant_id: str) -> list[Organization]:
        """Return every organization of a tenant."""
        ...

    async def save_team(self, team: Team) -> None:
        """Persist a team (insert or replace)."""
        ...

    async def get_team(self, team_id: str) -> Team | None:
        """Return the team with this id, or `None`."""
        ...

    async def list_teams(self, org_id: str) -> list[Team]:
        """Return every team of an organization."""
        ...

    async def save_membership(self, membership: Membership) -> None:
        """Persist a membership (insert or replace)."""
        ...

    async def list_memberships(self, user_id: str) -> list[Membership]:
        """Return every membership of a user."""
        ...

    async def delete_membership(self, membership_id: str) -> bool:
        """Delete a membership; `True` when one existed."""
        ...


@runtime_checkable
class LoginLimiter(Protocol):
    """Shared login-throttle seam behind `AuthService.login` (spec 0006, E3).

    The local ledger (`utils.check_login_allowed`) and the redis counter
    (`utils.RedisLoginLimiter`) both satisfy this structurally, so the service
    never knows which window it is checking.
    """

    async def check(self, ip: str) -> None:
        """Record one login attempt, rejecting an exhausted window.

        Args:
            ip: Client address (already extracted from headers or connection info).

        Raises:
            TooManyAttemptsError: When the IP exhausted its window.
        """
        ...
