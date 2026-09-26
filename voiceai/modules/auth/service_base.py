"""Auth service kernel: ctor, guards, minting, audit, tenancy projection (spec 0040).

`AuthServiceBase` holds everything the service slices share: the injected
store/limiter/JWT configuration, the no-oracle boundary guards, session and
JWT minting, the advisory audit writer, and the user→tenant projection the
credential resolvers stamp onto principals. The use-case slices
(`service_admin.AuthAdminMixin`, `service_identity.AuthIdentityMixin`) inherit
it, and `service.AuthService` composes all three — one responsibility per
file, one public surface. Split cited by spec 0040 (integrator): the identity
program grew `service.py` past the 800-line canonical budget.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

from voiceai.common.constants import DEFAULT_TENANT_ID
from voiceai.common.datetime_utils import utc_now
from voiceai.common.errors import InvalidRequestError
from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.modules.auth import constants as C
from voiceai.modules.auth.errors import AuthNotFoundError
from voiceai.modules.auth.exceptions import ensure_authenticated, ensure_permitted
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.ports import AuthStorePort, LoginLimiter
from voiceai.modules.auth.static_methods import issue_access_token, new_token, token_hash
from voiceai.modules.auth.utils import LocalLoginLimiter

__all__ = ["AuthServiceBase", "JwtSettings", "SessionTokens"]

logger: logging.Logger = get_logger("auth")


@dataclass(frozen=True)
class JwtSettings:
    """JWT configuration the service needs (built from `Environment` by the container).

    `None` in the service means the JWT flows stay dark (tests, single-proc dev
    without keys); every JWT method then raises `InvalidRequestError` instead of
    touching the store, so misconfiguration surfaces loudly rather than minting
    half a session.
    """

    private_key: str
    public_key: str
    issuer: str
    audience: str
    access_ttl_s: int
    refresh_ttl_s: int


@dataclass(frozen=True)
class SessionTokens:
    """One authenticated moment: legacy compat plus the JWT pair (T2 dual-write).

    `legacy_token` feeds the `otoba_session` cookie until the T7 cutover deletes
    it; `access_token` is the short-lived JWT (memory only); `refresh_token` is the
    opaque rotating secret (httpOnly `otoba_refresh` cookie, hash stored).
    """

    legacy_token: str
    access_token: str
    refresh_token: str


class AuthServiceBase:
    """Shared kernel for the auth service slices (never instantiated directly).

    Args:
        store: The auth persistence behind ``AuthStorePort`` (a fake in tests).
        limiter: The login-throttle seam (spec 0006, E3); `None` keeps the
            in-process ledger so existing behavior is unchanged.
        jwt: The JWT configuration, or `None` when the JWT flows stay dark.
    """

    def __init__(
        self,
        store: AuthStorePort,
        *,
        limiter: LoginLimiter | None = None,
        jwt: JwtSettings | None = None,
    ) -> None:
        self._store = store
        self._limiter: LoginLimiter = limiter if limiter is not None else LocalLoginLimiter()
        self._jwt = jwt

    @staticmethod
    def _ensure_same_org(row_org_id: str | None, principal: Principal, what: str) -> None:
        """Reject a cross-tenant row as not-found (spec 0020, M1b).

        The auth store is the tenant-discovery layer, so it stays unscoped and
        the service enforces the boundary: a row from another org reads exactly
        like a missing row — no existence oracle.

        Args:
            row_org_id: The owning org of the row being touched.
            principal: The acting caller.
            what: "User" or "Invite" for the not-found message.

        Raises:
            AuthNotFoundError: When the row belongs to another org.
        """
        if row_org_id != principal.org_id:
            raise AuthNotFoundError(f"{what} not found")

    @staticmethod
    def _ensure_same_tenant(row_tenant_id: str | None, principal: Principal, what: str) -> None:
        """Reject a cross-tenant row as not-found (spec 0040, Agent B).

        Tenant-hex analogue of `_ensure_same_org` with the same no-oracle
        rationale: a row from another tenant reads exactly like a missing row.

        Args:
            row_tenant_id: The owning tenant hex of the row being touched.
            principal: The acting caller.
            what: "User" or "Invite" for the not-found message.

        Raises:
            AuthNotFoundError: When the row belongs to another tenant.
        """
        if row_tenant_id != principal.tenant_id:
            raise AuthNotFoundError(f"{what} not found")

    async def _require_owner(self, principal: Principal) -> Principal:
        """Return the acting principal, gated to owners (401 then 403)."""
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("owner"), "Requires owner role or higher")
        return principal

    # -- sessions -----------------------------------------------------------

    async def _new_session(
        self,
        user_id: str,
        org_id: str,
        *,
        ttl_s: int,
        kind: Literal[
            "session", "ws-ticket"
        ] = C.SESSION_KIND,  # why: Literal mirrors SessionRecord.kind so mypy pins the ledger
        tenant_id: str | None = None,
    ) -> str:
        """Mint one session row, returning the RAW token (shown once — rule: secret)."""
        token = new_token()
        await self._store.save_session(
            SessionRecord(
                token_hash=token_hash(token),
                user_id=user_id,
                org_id=org_id,
                tenant_id=tenant_id,
                kind=kind,
                expires_at=utc_now() + timedelta(seconds=ttl_s),
            )
        )
        return token

    async def _default_tenant_hex(self) -> str | None:
        """Resolve the default tenant's object hex (spec 0040, creation stamping).

        Returns:
            The hex, or `None` when the tenants collection is unwired or the
            row is absent (backfill covers the gap — never invent an id).
        """
        get_by_slug = getattr(self._store, "get_tenant_by_slug", None)
        if get_by_slug is None:
            return None
        tenant = await get_by_slug("default")
        return tenant.tenant_id if tenant is not None else None

    async def _resolve_tenancy(self, user: User) -> tuple[str, list[str], dict[str, str]]:
        """Resolve a user's tenant hex plus team ids/roles (spec 0040, Agent B).

        Args:
            user: The already-resolved, non-disabled user row.

        Returns:
            `(tenant_id, teams, team_roles)`: the user's stamped tenant (or
            the pre-identity `"default"` fallback when the row carries none),
            the team ids from `list_memberships`, and the team id → role map
            the team gates read.
        """
        list_memberships = getattr(self._store, "list_memberships", None)
        memberships = await list_memberships(user.user_id) if list_memberships is not None else []
        teams = [membership.team_id for membership in memberships]
        team_roles: dict[str, str] = {membership.team_id: membership.role for membership in memberships}
        return (user.tenant_id or DEFAULT_TENANT_ID, teams, team_roles)

    async def _tenant_is_suspended(self, user: User) -> bool:
        """Report whether the user's tenant row is suspended (spec 0040, Agent B).

        Only an explicitly `suspended` tenant row denies the principal:
        pre-identity rows (`tenant_id is None`) and unknown hexes fail open
        so migration-era rows stay readable until the backfill stamps them.

        Args:
            user: The already-resolved, non-disabled user row.

        Returns:
            `True` when the user's tenant row exists and is suspended.
        """
        if user.tenant_id is None:
            return False
        get_tenant = getattr(self._store, "get_tenant", None)
        tenant = await get_tenant(user.tenant_id) if get_tenant is not None else None
        return tenant is not None and tenant.status == "suspended"

    def _require_jwt(self) -> JwtSettings:
        """Return the JWT configuration, failing loudly when the flows stay dark.

        Raises:
            InvalidRequestError: When no keys are configured — a caller bug or a
                deployment gap, never a store outage.
        """
        if self._jwt is None:
            raise InvalidRequestError("Token pair issuance is not configured")
        return self._jwt

    @staticmethod
    def _version_of(user: User) -> int:
        """Read a row's revocation stamp, defaulting legacy platform rows to zero."""
        version = getattr(user, "token_version", 0)
        return version if isinstance(version, int) else 0

    async def _new_jwt_pair(self, user: User) -> tuple[str, str]:
        """Mint an access JWT plus its opaque refresh row for an authenticated user.

        Args:
            user: The authenticated row (its `token_version` stamps both tokens).

        Returns:
            `(access_token, refresh_token_raw)` — the raw refresh is shown once;
            only its hash is stored, with `kind="refresh"`.
        """
        settings = self._require_jwt()
        moment = utc_now()
        access = issue_access_token(
            user_id=user.user_id,
            org_id=user.org_id,
            role=user.role,
            token_version=self._version_of(user),
            jti=new_token(),
            issuer=settings.issuer,
            audience=settings.audience,
            access_ttl_s=settings.access_ttl_s,
            private_key=settings.private_key,
            now=moment,
        )
        refresh = new_token()
        await self._store.save_session(
            SessionRecord(
                token_hash=token_hash(refresh),
                user_id=user.user_id,
                org_id=user.org_id,
                kind="refresh",
                expires_at=moment + timedelta(seconds=settings.refresh_ttl_s),
                token_version=user.token_version,
            )
        )
        return access, refresh

    async def _bump_version(self, user: User) -> None:
        """Stamp the next revocation version onto a user row (caller persists it).

        Legacy platform rows (quickstart's `RedisStore`, retired at T7) predate the
        stamp: stamping is skipped for them while session sweeps keep revoking, so a
        mixed deployment degrades to sweep-only revocation instead of crashing.
        """
        if "token_version" in type(user).model_fields:
            user.token_version = self._version_of(user) + 1

    async def audit(
        self,
        event_type: str,
        *,
        user_id: str | None = None,
        email: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Append one audit row stamped with the subject's tenant; NEVER fails the op.

        The stamp is the subject's `tenant_id`, falling back to its `org_id`
        for pre-identity rows — exactly what the spec-0020 backfill derives
        for the same row, so pre-migration events stay readable under both the
        old org reads and the new tenant reads, and the spec-0040 rewrite
        converts both sides to hex together. Post-migration rows always carry
        hex, so the fallback never fires there. Anonymous events (no subject)
        stay unstamped and hidden, as before.
        """
        try:
            tenant_id: str | None = None
            if user_id is not None:
                subject = await self._store.get_user(user_id)
                if subject is not None:
                    tenant_id = subject.tenant_id or subject.org_id
            await self._store.add_auth_event(
                AuthEvent(
                    event_id=new_id("evt"),
                    type=event_type,
                    user_id=user_id,
                    email=email,
                    detail=detail,
                    created_at=utc_now(),
                    tenant_id=tenant_id,
                )
            )
        except Exception:  # noqa: BLE001 - audit is advisory by design
            logger.warning("auth audit write failed", exc_info=True)
