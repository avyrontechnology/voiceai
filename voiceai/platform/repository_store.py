"""Legacy platform store over the greenfield repositories (spec 0048, Slice A).

The frozen ``platform/router.py`` talks to the ``MemoryStore`` surface. This module keeps
that surface and moves the bytes: every non-auth family becomes a tenant-stamped
``PlatformRow`` in its own ``platform_<family>`` collection (``Collections.PLATFORM_*``),
driven by whichever repository the container built (in-memory or Motor); the auth
families delegate to the greenfield ``AuthStorePort`` so users, sessions, invites, API
keys, audit events and the denylist have exactly one home.

Bridge, not target: M6 (spec 0018) migrates each family into its module and drops these
collections. Documented debts until then:

* ``_all`` materialises every row of the tenant: the legacy surface lists everything
  and filters, sorts and windows in Python. Since spec 0050 the read is tenant-paged
  at the driver (``walk_pages`` over the scoped repository), so it costs that tenant's
  pages, never the whole collection — but it is still unbounded in the tenant's rows.
* ``scan_family`` (the unauthenticated inbound lookup, spec 0047) is the one read that
  pages a whole collection across tenants, by design.
* ``insert`` on an existing id replaces it regardless of tenant, exactly like the auth
  store (legacy ids are unguessable hex, never user input).
* Ledger order on Motor is ``created_at`` then ``_id``; same-instant entries may swap.
  The wallet module serves the real ledger routes.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any, Final

from pydantic import Field

from voiceai.common.errors import ConfigurationError, NotFoundError
from voiceai.common.tenancy import current_tenant
from voiceai.database.base import BaseFields
from voiceai.database.constants import Collections
from voiceai.database.repository import BaseRepository, walk_pages
from voiceai.database.scoped import TenantScopedRepository
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.platform.models import ApiKey, AuthEvent, Invite, SessionRecord, User
from voiceai.platform.store import SINGLETON_ORGANIZATION, MemoryStore

__all__ = ["FAMILY_COLLECTIONS", "PLATFORM_COLLECTIONS", "PlatformRow", "RepositoryPlatformStore"]

#: Legacy family (the `MemoryStore` collection name) → its bridge collection.
FAMILY_COLLECTIONS: Final[Mapping[str, Collections]] = {
    "executions": Collections.PLATFORM_EXECUTIONS,
    "batches": Collections.PLATFORM_BATCHES,
    "numbers": Collections.PLATFORM_NUMBERS,
    "kbs": Collections.PLATFORM_KBS,
    "tools": Collections.PLATFORM_TOOLS,
    "webhooks": Collections.PLATFORM_WEBHOOKS,
    "inbound": Collections.PLATFORM_INBOUND,
    "voices": Collections.PLATFORM_VOICES,
    "vector": Collections.PLATFORM_VECTOR,
    "subaccounts": Collections.PLATFORM_SUBACCOUNTS,
    "integrations": Collections.PLATFORM_INTEGRATIONS,
    "graphs": Collections.PLATFORM_GRAPHS,
    "graph_versions": Collections.PLATFORM_GRAPH_VERSIONS,
    "workflows": Collections.PLATFORM_WORKFLOWS,
    "workflow_versions": Collections.PLATFORM_WORKFLOW_VERSIONS,
    "workflow_runs": Collections.PLATFORM_WORKFLOW_RUNS,
    "workflow_campaigns": Collections.PLATFORM_WORKFLOW_CAMPAIGNS,
}

#: Every bridge collection the container must supply a repository for.
PLATFORM_COLLECTIONS: Final[tuple[Collections, ...]] = (
    *FAMILY_COLLECTIONS.values(),
    Collections.PLATFORM_LEDGER,
    Collections.PLATFORM_SINGLETONS,
)

_MISSING_REPOSITORIES: Final[str] = "platform store is missing repositories"
_UNKNOWN_FAMILY: Final[str] = "unknown platform family"
_DETAIL_COLLECTIONS: Final[str] = "collections"
_DETAIL_FAMILY: Final[str] = "family"
#: The one auth-store family `reset_platform` still walks (legacy parity: a workspace
#: reset revokes every API key; users/sessions/invites/audit are never wiped).
_API_KEYS_FAMILY: Final[str] = "api_keys"
#: Singleton ids carry the tenant because `_id` is global to the collection.
_SINGLETON_ID_SEPARATOR: Final[str] = ":"


class PlatformRow(BaseFields):
    """One legacy platform document: the verbatim payload under a tenant-stamped envelope."""

    payload: dict[str, Any] = Field(default_factory=dict)


def _expired(expires_at: datetime) -> bool:
    """Report whether a session expiry (naive values are UTC) lies in the past."""
    aware = expires_at if expires_at.tzinfo is not None else expires_at.replace(tzinfo=timezone.utc)
    return aware <= datetime.now(timezone.utc)


class RepositoryPlatformStore(MemoryStore):
    """`MemoryStore` surface persisted through the greenfield repositories.

    The tenant is read from the ambient context on every call, never at
    construction, so the container may hold this store as a singleton.

    Args:
        auth_store: The greenfield auth store every auth family delegates to.
        repositories: One `PlatformRow` repository per member of `PLATFORM_COLLECTIONS`.

    Raises:
        ConfigurationError: When a bridge collection has no repository.
    """

    def __init__(
        self,
        auth_store: AuthStorePort,
        repositories: Mapping[Collections, BaseRepository[PlatformRow]],
    ) -> None:
        super().__init__()
        missing = [collection.value for collection in PLATFORM_COLLECTIONS if collection not in repositories]
        if missing:
            raise ConfigurationError(_MISSING_REPOSITORIES, details={_DETAIL_COLLECTIONS: missing})
        self._auth = auth_store
        self._repositories: dict[Collections, BaseRepository[PlatformRow]] = dict(repositories)

    # -- primitives (the only MemoryStore surface this backend overrides) ---------------

    def _scoped(self, collection: Collections) -> TenantScopedRepository[PlatformRow]:
        """Pin a bridge repository to the ambient request tenant (read per call)."""
        return TenantScopedRepository(self._repositories[collection], current_tenant().tenant_id, collection)

    @staticmethod
    def _collection(family: str) -> Collections:
        """Resolve a legacy family name to its bridge collection."""
        try:
            return FAMILY_COLLECTIONS[family]
        except KeyError as exc:
            raise ConfigurationError(_UNKNOWN_FAMILY, details={_DETAIL_FAMILY: family}) from exc

    async def _pages(self, collection: Collections) -> list[PlatformRow]:
        """Every active row of a collection across tenants, oldest first (paged).

        Tenant-blind on purpose: only `scan_family` may call it.
        """
        return [row async for row in walk_pages(self._repositories[collection])]

    async def _rows(self, collection: Collections) -> list[PlatformRow]:
        """Every active row of this tenant, oldest first.

        Walks the tenant-scoped view (spec 0050): the driver pages this tenant's rows
        alone, so another tenant's volume never costs this one a query and no row
        past the first page is lost.
        """
        return [row async for row in walk_pages(self._scoped(collection))]

    # -- tenant-blind read for the inbound lookup (spec 0047 seam) -----------------------

    async def scan_family(self, family: str) -> list[dict[str, Any]]:
        """Every tenant's active rows of one family, oldest first — the inbound lookup's read.

        Carrier webhooks arrive unauthenticated (spec 0047): a called number maps to at
        most one agent row and the agent's tenant binds the call, so this read is
        tenant-blind by construction. Nothing else may use it.
        """
        return [row.payload for row in await self._pages(self._collection(family))]

    # -- backfill entry points (spec 0048, Slice C) -------------------------------------

    async def restore(self, family: str, item_id: str, payload: dict[str, Any]) -> bool:
        """Write one legacy row verbatim unless the tenant already holds that id.

        Atlas is truth: a row that exists (from an earlier run or a post-cutover edit)
        is never overwritten, which keeps re-runs safe.

        Returns:
            `True` when the row was written, `False` when it already existed.
        """
        if await self._get(family, item_id) is not None:
            return False
        await self._put(family, item_id, payload)
        return True

    async def restore_organization(self, payload: dict[str, Any]) -> bool:
        """Seed the organization settings singleton once; an existing one wins."""
        if await self._singleton_get(SINGLETON_ORGANIZATION) is not None:
            return False
        await self._singleton_put(SINGLETON_ORGANIZATION, payload)
        return True

    async def _put(self, collection: str, item_id: str, payload: dict[str, Any]) -> None:
        await self._scoped(self._collection(collection)).insert(PlatformRow(id=item_id, payload=payload))

    async def _get(self, collection: str, item_id: str) -> dict[str, Any] | None:
        row = await self._scoped(self._collection(collection)).get(item_id)
        return None if row is None else row.payload

    async def _all(self, collection: str) -> list[dict[str, Any]]:
        return [row.payload for row in await self._rows(self._collection(collection))]

    async def _delete(self, collection: str, item_id: str) -> bool:
        try:
            return await self._scoped(self._collection(collection)).soft_delete(item_id)
        except NotFoundError:
            return False

    async def _clear(self, collection: str) -> int:
        if collection == _API_KEYS_FAMILY:  # legacy reset wipes keys too; they live in the auth store
            keys = await self._auth.list_api_keys()
            return sum([await self._auth.delete_api_key(key.key_id) for key in keys])
        cleared = 0
        for row in await self._rows(self._collection(collection)):
            if row.id is not None and await self._delete(collection, row.id):
                cleared += 1
        return cleared

    @staticmethod
    def _singleton_id(name: str) -> str:
        """Return the tenant-qualified id of a singleton document."""
        return f"{current_tenant().tenant_id}{_SINGLETON_ID_SEPARATOR}{name}"

    async def _singleton_get(self, name: str) -> dict[str, Any] | None:
        row = await self._scoped(Collections.PLATFORM_SINGLETONS).get(self._singleton_id(name))
        return None if row is None else row.payload

    async def _singleton_put(self, name: str, payload: dict[str, Any]) -> None:
        row = PlatformRow(id=self._singleton_id(name), payload=payload)
        await self._scoped(Collections.PLATFORM_SINGLETONS).insert(row)

    async def _ledger_append(self, payload: dict[str, Any]) -> None:
        await self._scoped(Collections.PLATFORM_LEDGER).insert(PlatformRow(payload=payload))

    async def _ledger_recent(self, limit: int) -> list[dict[str, Any]]:
        if limit <= 0:
            return []
        rows = await self._rows(Collections.PLATFORM_LEDGER)
        return [row.payload for row in reversed(rows[-limit:])]

    async def _ledger_clear(self) -> int:
        cleared = 0
        scoped = self._scoped(Collections.PLATFORM_LEDGER)
        for row in await self._rows(Collections.PLATFORM_LEDGER):
            if row.id is not None and await scoped.soft_delete(row.id):
                cleared += 1
        return cleared

    # -- auth families: one home, the greenfield auth store -----------------------------

    async def save_user(self, user: User) -> None:
        """Persist a user through the auth store."""
        await self._auth.save_user(user)

    async def get_user(self, user_id: str) -> User | None:
        """Return the active user with this id, or `None`."""
        return await self._auth.get_user(user_id)

    async def get_user_by_email(self, email: str) -> User | None:
        """Return the active user with this email, or `None` (indexed)."""
        return await self._auth.get_user_by_email(email)

    async def list_users(self) -> list[User]:
        """Return every active user."""
        return await self._auth.list_users()

    async def count_users(self) -> int:
        """Return the number of active users."""
        return await self._auth.count_users()

    async def delete_user(self, user_id: str) -> bool:
        """Retire a user; `True` when one was active."""
        return await self._auth.delete_user(user_id)

    async def delete_user_sessions(self, user_id: str) -> int:
        """Retire every session of a user and return the count."""
        return await self._auth.delete_user_sessions(user_id)

    async def save_session(self, session: SessionRecord) -> None:
        """Persist a session record through the auth store."""
        await self._auth.save_session(session)

    async def get_session(self, token_hash: str) -> SessionRecord | None:
        """Return the live session for a token hash; an expired one is retired and reads as absent."""
        session = await self._auth.get_session(token_hash)
        if session is None:
            return None
        if _expired(session.expires_at):
            await self._auth.delete_session(token_hash)
            return None
        return session

    async def delete_session(self, token_hash: str) -> bool:
        """Retire a session; `True` when one was active."""
        return await self._auth.delete_session(token_hash)

    async def list_user_sessions(self, user_id: str) -> list[SessionRecord]:
        """Return every active session record of a user."""
        return await self._auth.list_user_sessions(user_id)

    async def save_invite(self, invite: Invite) -> None:
        """Persist an invite through the auth store."""
        await self._auth.save_invite(invite)

    async def get_invite(self, invite_id: str) -> Invite | None:
        """Return the active invite with this id, or `None`."""
        return await self._auth.get_invite(invite_id)

    async def get_invite_by_token_hash(self, token_hash: str) -> Invite | None:
        """Return the invite with this token digest, or `None` (indexed)."""
        return await self._auth.get_invite_by_token_hash(token_hash)

    async def list_invites(self) -> list[Invite]:
        """Return every active invite."""
        return await self._auth.list_invites()

    async def delete_invite(self, invite_id: str) -> bool:
        """Retire an invite; `True` when one was active."""
        return await self._auth.delete_invite(invite_id)

    async def save_api_key(self, key: ApiKey) -> None:
        """Persist an API key through the auth store."""
        await self._auth.save_api_key(key)

    async def get_api_key_by_hash(self, key_hash: str) -> ApiKey | None:
        """Return the API key with this secret hash, or `None` (indexed, no scan)."""
        return await self._auth.get_api_key_by_hash(key_hash)

    async def list_api_keys(self) -> list[ApiKey]:
        """Return every active API key."""
        return await self._auth.list_api_keys()

    async def delete_api_key(self, key_id: str) -> bool:
        """Retire an API key; `True` when one was active."""
        return await self._auth.delete_api_key(key_id)

    async def add_auth_event(self, event: AuthEvent) -> None:
        """Append one audit event through the auth store."""
        await self._auth.add_auth_event(event)

    async def list_auth_events(self, limit: int = 100) -> list[AuthEvent]:
        """Return recent audit events, newest first, bounded by `limit`."""
        return await self._auth.list_auth_events(limit)

    async def save_revoked(self, token: Any) -> None:  # why: the legacy surface types the token as Any
        """Deny one access token by JWT id through the auth store."""
        await self._auth.save_revoked(token)

    async def is_revoked(self, jti: str) -> bool:
        """Report whether a JWT id was denied."""
        return await self._auth.is_revoked(jti)
