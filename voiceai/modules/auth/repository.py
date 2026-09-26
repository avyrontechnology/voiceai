"""Mongo-backed auth store: the greenfield system of record (T2).

Satisfies :class:`voiceai.modules.auth.ports.AuthStorePort` over one
:class:`voiceai.database.repository.BaseRepository` per collection (Atlas in prod,
in-memory in tests) plus an optional Redis read-through cache for the revocation
fast path. Natural keys pin `id` (`user_id`, `token_hash`, `invite_id`, `key_id`,
`event_id`, `jti`), so identity is unchanged from the legacy stores; deletes are
soft (AGENTS.md rule 5) where the legacy stores destroy — reads skip inactive rows
either way, and the T7 cutover retires the difference. Spec 0040 adds the
identity tail (tenants, organizations, teams, memberships) behind optional
repositories: wired collections delegate, `None` falls back to process-local
rows so pre-cutover constructions keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Final

from voiceai.common.constants import MAX_PAGE_SIZE
from voiceai.common.datetime_utils import utc_now
from voiceai.common.logger import get_logger
from voiceai.common.pagination import PaginationParams
from voiceai.database.base import BaseFields
from voiceai.database.repository import BaseRepository
from voiceai.modules.auth import constants as C
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

if TYPE_CHECKING:  # pragma: no cover - typing only, so the module imports without a driver
    from redis.asyncio import Redis

__all__ = ["MongoAuthStore"]

_LOGGER_MODULE: Final[str] = "auth.repository"
_LOG_DENYLIST_CACHE_FAILED: Final[str] = "revocation cache failed, failing open to the store (%s)"
_PAGE_SIZE: Final[int] = MAX_PAGE_SIZE


def _pin(model: BaseFields, natural_id: str) -> BaseFields:
    """Pin a model's storage id to its natural key, returning it for chaining."""
    model.id = natural_id
    return model


class MongoAuthStore:
    """Auth persistence over indexed collections with a cached denylist.

    Args:
        users: The `users` collection (`email` unique index, see `scripts/migrate.py`).
        sessions: The `sessions` collection (`token_hash` unique, `user_id` index,
            TTL on `expires_at`).
        invites: The `invites` collection (`token_hash` unique index, TTL on `expires_at`).
        keys: The `api_keys` collection (`key_hash` unique index).
        events: The `auth_events` collection (descending `created_at` reads).
        revoked: The `revoked_tokens` collection (`jti` unique, TTL on `expires_at`).
        tenants: The `tenants` collection (`slug` unique), or `None` for a
            process-local fallback (integrator wires the collection at cutover;
            `None` keeps pre-cutover constructions working).
        organizations: The `organizations` collection, or `None` (same fallback).
        teams: The `teams` collection, or `None` (same fallback).
        memberships: The `memberships` collection, or `None` (same fallback).
        cache: Redis read-through for `is_revoked`, or `None` to read the store
            directly (tests, single-proc dev).
    """

    def __init__(
        self,
        *,
        users: BaseRepository[User],
        sessions: BaseRepository[SessionRecord],
        invites: BaseRepository[Invite],
        keys: BaseRepository[ApiKey],
        events: BaseRepository[AuthEvent],
        revoked: BaseRepository[RevokedToken],
        tenants: BaseRepository[Tenant] | None = None,
        organizations: BaseRepository[Organization] | None = None,
        teams: BaseRepository[Team] | None = None,
        memberships: BaseRepository[Membership] | None = None,
        cache: Redis | None = None,
    ) -> None:
        self._users = users
        self._sessions = sessions
        self._invites = invites
        self._keys = keys
        self._events = events
        self._revoked = revoked
        self._tenants = tenants
        self._organizations = organizations
        self._teams = teams
        self._memberships = memberships
        self._cache = cache
        self._tenant_rows: dict[str, Tenant] = {}
        self._organization_rows: dict[str, Organization] = {}
        self._team_rows: dict[str, Team] = {}
        self._membership_rows: dict[str, Membership] = {}

    async def _all(self, repo: BaseRepository[Any]) -> list[Any]:  # why: heterogeneous owned collections
        """Return every active document of a collection, oldest first."""
        items: list[Any] = []  # why: heterogeneous owned collections
        page_number = 1
        while True:
            page = await repo.list(PaginationParams(page=page_number, page_size=_PAGE_SIZE))
            items.extend(page.items)
            if not page.has_next:
                return items
            page_number += 1

    async def save_user(self, user: User) -> None:
        """Persist a user (insert or replace) keyed by `user_id`."""
        _pin(user, user.user_id)
        await self._users.insert(user)

    async def get_user(self, user_id: str) -> User | None:
        """Return the active user with this id, or `None`."""
        return await self._users.get(user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        """Return the active user with this email, or `None` (indexed, no scan)."""
        return await self._users.find_one("email", email.strip().lower())

    async def list_users(self) -> list[User]:
        """Return every active user, oldest first."""
        return await self._all(self._users)

    async def count_users(self) -> int:
        """Return the number of active users (count query, no document fetch)."""
        page = await self._users.list(PaginationParams(page=1, page_size=1))
        return page.total

    async def delete_user(self, user_id: str) -> bool:
        """Soft-delete a user; `True` when one was active (rule 5)."""
        return await self._users.soft_delete(user_id)

    async def delete_user_sessions(self, user_id: str) -> int:
        """Soft-delete every session and refresh row of a user; return the count."""
        count = 0
        for session in await self._sessions.find_many("user_id", user_id):
            if session.id and await self._sessions.soft_delete(session.id):
                count += 1
        return count

    async def save_session(self, session: SessionRecord) -> None:
        """Persist a session or refresh record keyed by `token_hash`."""
        _pin(session, session.token_hash)
        await self._sessions.insert(session)

    async def get_session(self, token_hash: str) -> SessionRecord | None:
        """Return the active session for a token hash, or `None`."""
        return await self._sessions.get(token_hash)

    async def delete_session(self, token_hash: str) -> bool:
        """Soft-delete a session; `True` when one was active."""
        return await self._sessions.soft_delete(token_hash)

    async def save_invite(self, invite: Invite) -> None:
        """Persist an invite keyed by `invite_id`."""
        _pin(invite, invite.invite_id)
        await self._invites.insert(invite)

    async def get_invite(self, invite_id: str) -> Invite | None:
        """Return the active invite with this id, or `None`."""
        return await self._invites.get(invite_id)

    async def get_invite_by_token_hash(self, token_hash: str) -> Invite | None:
        """Return the invite with this token digest, or `None` (indexed, no scan)."""
        return await self._invites.find_one("token_hash", token_hash)

    async def list_invites(self) -> list[Invite]:
        """Return every active invite, oldest first."""
        return await self._all(self._invites)

    async def delete_invite(self, invite_id: str) -> bool:
        """Soft-delete an invite; `True` when one was active."""
        return await self._invites.soft_delete(invite_id)

    async def list_api_keys(self) -> list[ApiKey]:
        """Return every active API key, oldest first."""
        return await self._all(self._keys)

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        """Return the API key with this secret hash, or `None` (indexed, no scan)."""
        return await self._keys.find_one("key_hash", key_hash)

    async def save_api_key(self, key: ApiKey) -> None:
        """Persist an API key keyed by `key_id`."""
        _pin(key, key.key_id)
        await self._keys.insert(key)

    async def add_auth_event(self, event: AuthEvent) -> None:
        """Append one audit event keyed by `event_id`."""
        _pin(event, event.event_id)
        await self._events.insert(event)

    async def list_auth_events(self, limit: int = 100) -> list[AuthEvent]:
        """Return recent audit events, newest first, bounded by `limit`."""
        items = await self._all(self._events)
        items.sort(key=lambda event: event.created_at, reverse=True)
        return items[: max(0, limit)]

    async def list_user_sessions(self, user_id: str) -> list[SessionRecord]:
        """Return every active session and refresh record of a user."""
        return list(await self._sessions.find_many("user_id", user_id))

    async def save_tenant(self, tenant: Tenant) -> None:
        """Persist a tenant keyed by `tenant_id`."""
        _pin(tenant, tenant.tenant_id)
        if self._tenants is not None:
            await self._tenants.insert(tenant)
        else:
            self._tenant_rows[tenant.tenant_id] = tenant

    async def get_tenant(self, tenant_id: str) -> Tenant | None:
        """Return the active tenant with this id, or `None`."""
        if self._tenants is not None:
            return await self._tenants.get(tenant_id)
        row = self._tenant_rows.get(tenant_id)
        return row if row is not None and row.is_active else None

    async def get_tenant_by_slug(self, slug: str) -> Tenant | None:
        """Return the active tenant with this slug, or `None` (indexed, no scan)."""
        if self._tenants is not None:
            return await self._tenants.find_one("slug", slug)
        return next((t for t in self._tenant_rows.values() if t.slug == slug and t.is_active), None)

    async def list_tenants(self) -> list[Tenant]:
        """Return every active tenant, oldest first."""
        if self._tenants is not None:
            return await self._all(self._tenants)
        return [t for t in self._tenant_rows.values() if t.is_active]

    async def save_organization(self, organization: Organization) -> None:
        """Persist an organization keyed by `org_id`."""
        _pin(organization, organization.org_id)
        if self._organizations is not None:
            await self._organizations.insert(organization)
        else:
            self._organization_rows[organization.org_id] = organization

    async def get_organization(self, org_id: str) -> Organization | None:
        """Return the active organization with this id, or `None`."""
        if self._organizations is not None:
            return await self._organizations.get(org_id)
        row = self._organization_rows.get(org_id)
        return row if row is not None and row.is_active else None

    async def list_organizations(self, tenant_id: str) -> list[Organization]:
        """Return every active organization of a tenant, oldest first."""
        if self._organizations is not None:
            return list(await self._organizations.find_many("tenant_id", tenant_id))
        return [o for o in self._organization_rows.values() if o.tenant_id == tenant_id and o.is_active]

    async def save_team(self, team: Team) -> None:
        """Persist a team keyed by `team_id`."""
        _pin(team, team.team_id)
        if self._teams is not None:
            await self._teams.insert(team)
        else:
            self._team_rows[team.team_id] = team

    async def get_team(self, team_id: str) -> Team | None:
        """Return the active team with this id, or `None`."""
        if self._teams is not None:
            return await self._teams.get(team_id)
        row = self._team_rows.get(team_id)
        return row if row is not None and row.is_active else None

    async def list_teams(self, org_id: str) -> list[Team]:
        """Return every active team of an organization, oldest first."""
        if self._teams is not None:
            return list(await self._teams.find_many("org_id", org_id))
        return [t for t in self._team_rows.values() if t.org_id == org_id and t.is_active]

    async def save_membership(self, membership: Membership) -> None:
        """Persist a membership keyed by `membership_id`."""
        _pin(membership, membership.membership_id)
        if self._memberships is not None:
            await self._memberships.insert(membership)
        else:
            self._membership_rows[membership.membership_id] = membership

    async def list_memberships(self, user_id: str) -> list[Membership]:
        """Return every active membership of a user, oldest first."""
        if self._memberships is not None:
            return list(await self._memberships.find_many("user_id", user_id))
        return [m for m in self._membership_rows.values() if m.user_id == user_id and m.is_active]

    async def delete_membership(self, membership_id: str) -> bool:
        """Soft-delete a membership; `True` when one was active (rule 5)."""
        if self._memberships is not None:
            return await self._memberships.soft_delete(membership_id)
        row = self._membership_rows.get(membership_id)
        if row is None or not row.is_active:
            return False
        row.is_active = False
        return True

    async def save_revoked(self, token: RevokedToken) -> None:
        """Deny one access token, in the store and in the revocation cache."""
        _pin(token, token.jti)
        await self._revoked.insert(token)
        await self._cache_deny(token)

    async def is_revoked(self, jti: str) -> bool:
        """Report whether a JWT id was denied (cache-fast, store-backed).

        A cache outage fails OPEN with a log line — the user row's `token_version`
        check still bounds any miss to the access-token TTL (the throttle-fallback
        precedent in `utils.RedisLoginLimiter`).
        """
        if self._cache is None:
            return await self._revoked.get(jti) is not None
        try:
            if await self._cache.get(self._deny_key(jti)) is not None:
                return True
        except Exception as exc:  # noqa: BLE001 - outage degrades to the store read below
            get_logger(_LOGGER_MODULE).warning(_LOG_DENYLIST_CACHE_FAILED, type(exc).__name__)
            return await self._revoked.get(jti) is not None
        denied = await self._revoked.get(jti) is not None
        if denied:
            await self._cache_deny_by_jti(jti)
        return denied

    @staticmethod
    def _deny_key(jti: str) -> str:
        """Return the revocation-cache key for one JWT id."""
        return f"{C.DENYLIST_KEY_PREFIX}{jti}"

    async def _cache_deny(self, token: RevokedToken) -> None:
        """Write one denylist entry to the cache with the token's remaining TTL."""
        if self._cache is None:
            return
        ttl = int((token.expires_at - utc_now()).total_seconds())
        if ttl <= 0:
            return
        try:
            await self._cache.set(self._deny_key(token.jti), "1", ex=ttl)
        except Exception as exc:  # noqa: BLE001 - advisory cache; the store write above stands
            get_logger(_LOGGER_MODULE).warning(_LOG_DENYLIST_CACHE_FAILED, type(exc).__name__)

    async def _cache_deny_by_jti(self, jti: str) -> None:
        """Populate the cache after a store hit (best-effort, advisory only)."""
        if self._cache is None:
            return
        try:
            stored = await self._revoked.get(jti)
            if stored is not None:
                await self._cache_deny(stored)
        except Exception as exc:  # noqa: BLE001 - advisory cache; the store answer stands
            get_logger(_LOGGER_MODULE).warning(_LOG_DENYLIST_CACHE_FAILED, type(exc).__name__)
