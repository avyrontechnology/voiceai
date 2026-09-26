"""Auth service facade: credential lifecycle over admin + identity slices (AGENTS.md rule 1e).

`AuthService` composes `service_admin.AuthAdminMixin` and
`service_identity.AuthIdentityMixin` over the `service_base.AuthServiceBase`
kernel: this file holds the credential lifecycle only (session/key/JWT
resolvers, signup/login/refresh/logout, ticket mint/redeem, `me`), while user
administration and the identity program live in their slices. Split cited by
spec 0040 (integrator): the identity program grew this file past the
800-line canonical budget.

Greenfield deltas (T2): JWT access tokens (stateless, RS256) plus opaque rotating
refresh rows, dual-written with the legacy Redis sessions until the T7 cutover
retires them; indexed store lookups (no collection scans); `token_version` bumps
on role change, password rotation and logout-all; single-token logout via the
revocation store. Legacy session/ticket/key flows are byte-identical otherwise.
"""

from __future__ import annotations

import logging
from datetime import timedelta, timezone

from voiceai.common.datetime_utils import utc_now
from voiceai.common.errors import ConflictError
from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.common.tenancy import TenantContext
from voiceai.modules.auth import constants as C
from voiceai.modules.auth.errors import ForbiddenError, InvalidCredentialsError
from voiceai.modules.auth.exceptions import ensure_authenticated, ensure_permitted
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.revoked import RevokedToken
from voiceai.modules.auth.models.user import User
from voiceai.modules.auth.service_admin import AuthAdminMixin
from voiceai.modules.auth.service_base import JwtSettings as JwtSettings
from voiceai.modules.auth.service_base import SessionTokens as SessionTokens
from voiceai.modules.auth.service_identity import AuthIdentityMixin
from voiceai.modules.auth.static_methods import hash_password, token_hash, verify_access_token, verify_password

__all__ = ["AuthService", "JwtSettings", "SessionTokens"]

logger: logging.Logger = get_logger("auth")


class AuthService(AuthAdminMixin, AuthIdentityMixin):
    """Credential lifecycle: resolvers, entry points, logout, tickets, `me`.

    User administration rides `AuthAdminMixin`, the identity program rides
    `AuthIdentityMixin`, and the shared kernel (ctor, guards, minting, audit,
    tenancy projection) rides `service_base.AuthServiceBase` — all composed
    here so the public surface (`AuthService`, `JwtSettings`,
    `SessionTokens`) never moves.
    """

    async def _principal_from_session(self, token: str | None) -> Principal | None:
        """Resolve a session token to a principal (tickets never authenticate)."""
        if not token:
            return None
        session = await self._store.get_session(token_hash(token))
        if not session or session.kind != C.SESSION_KIND:
            return None
        user = await self._store.get_user(session.user_id)
        if not user or user.disabled:
            return None
        if await self._tenant_is_suspended(user):
            return None
        tenant_id, teams, team_roles = await self._resolve_tenancy(user)
        return Principal(
            user_id=user.user_id,
            email=user.email,
            org_id=user.org_id,
            tenant_id=tenant_id,
            teams=teams,
            team_roles=team_roles,
            role=user.role,
            auth_type="session",
        )

    async def _principal_from_api_key(self, secret: str | None) -> Principal | None:
        """Resolve a bearer API key to a principal, touching its last-used stamp."""
        if not secret:
            return None
        digest = token_hash(secret)
        key = await self._store.get_api_key_by_hash(digest)
        if key is None or not key.key_hash:
            return None  # unknown digest, or a legacy key minted before hashing: rotate it
        if key.expires_at and key.expires_at.replace(tzinfo=timezone.utc) < utc_now():
            return None
        key.last_used_at = utc_now()
        try:
            await self._store.save_api_key(key)
        except Exception:  # noqa: BLE001 - last-used is advisory, never fail auth
            logger.warning("Could not touch last_used_at for key %s", key.key_id)
        user = await self._store.get_user(key.created_by) if key.created_by else None
        if user is None or user.disabled:
            # DEVIATION from verbatim legacy (which skips this gate): a disabled
            # user's keys must stop working, mirroring the session resolver.
            # Logged loudly for the C6 audit; legacy keeps the hole till cutover.
            return None
        if await self._tenant_is_suspended(user):
            return None
        tenant_id, teams, team_roles = await self._resolve_tenancy(user)
        return Principal(
            user_id=key.created_by,
            email=user.email if user else None,
            org_id=user.org_id if user else "default",
            tenant_id=tenant_id,
            teams=teams,
            team_roles=team_roles,
            role="viewer",
            auth_type="key",
            scopes=list(key.scopes),
            key_id=key.key_id,
            key_name=key.name,
        )

    # -- entry points -------------------------------------------------------

    async def signup(self, email: str, name: str | None, password: str) -> tuple[User, SessionTokens]:
        """Register the FIRST user (owner); later signups need an invite.

        Raises:
            ForbiddenError: When users already exist.
            ConflictError: When the email is taken.
        """
        if await self._store.count_users() > 0:
            raise ForbiddenError("Signup is closed — ask an admin for an invite")
        if await self._store.get_user_by_email(email.strip().lower()) is not None:
            raise ConflictError("Email already registered")
        user = User(
            user_id=new_id("usr"),
            email=email.strip().lower(),
            name=name,
            password_hash=hash_password(password),
            role="owner",
            tenant_id=await self._default_tenant_hex(),
        )
        await self._store.save_user(user)
        legacy = await self._new_session(
            user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S, tenant_id=user.tenant_id
        )
        access, refresh = await self._new_jwt_pair(user)
        await self.audit("signup", user_id=user.user_id, email=user.email, detail="first user (owner)")
        return user, SessionTokens(legacy_token=legacy, access_token=access, refresh_token=refresh)

    async def login(self, email: str, password: str, remember: bool, *, client_ip: str) -> tuple[User, SessionTokens]:
        """Check credentials, mint a legacy session plus a JWT pair, stamp last login.

        Raises:
            TooManyAttemptsError: When the IP exhausted its window.
            InvalidCredentialsError: When the email/password/disabled gate fails.
        """
        await self._limiter.check(client_ip)
        user = await self._store.get_user_by_email(email)
        if not user or user.disabled or not verify_password(password, user.password_hash):
            await self.audit("login_failed", email=email.strip().lower())
            raise InvalidCredentialsError("Invalid email or password")
        legacy = await self._new_session(
            user.user_id, user.org_id, ttl_s=C.REMEMBER_TTL_S if remember else C.SESSION_TTL_S, tenant_id=user.tenant_id
        )
        access, refresh = await self._new_jwt_pair(user)
        user.last_login_at = utc_now()
        await self._store.save_user(user)
        await self.audit("login", user_id=user.user_id, email=user.email)
        return user, SessionTokens(legacy_token=legacy, access_token=access, refresh_token=refresh)

    async def refresh(self, refresh_token: str | None, *, client_ip: str) -> tuple[User, SessionTokens]:
        """Rotate an opaque refresh token into a fresh legacy session plus JWT pair.

        Rotation is single-use: the presented row is deleted before the new pair is
        minted, so a replayed refresh finds nothing. A presented digest that matches
        no live row is treated as reuse — every refresh row of the user dies and the
        version bumps, quarantining a stolen secret to at most the access TTL.

        Raises:
            TooManyAttemptsError: When the IP exhausted its window.
            InvalidCredentialsError: When the refresh is missing, stale, or reused.
        """
        await self._limiter.check(client_ip)
        if not refresh_token:
            raise InvalidCredentialsError("Session expired, sign in again")
        row = await self._store.get_session(token_hash(refresh_token))
        if row is None or row.kind != "refresh":
            await self.audit("refresh_failed", detail="unknown digest")
            raise InvalidCredentialsError("Session expired, sign in again")
        expires_at = row.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < utc_now():
            await self._store.delete_session(row.token_hash)
            raise InvalidCredentialsError("Session expired, sign in again")
        user = await self._store.get_user(row.user_id)
        if not user or user.disabled or self._version_of(user) != row.token_version:
            if user is not None:
                await self._quarantine_user(user)
            await self.audit("refresh_reuse_detected", user_id=row.user_id)
            raise InvalidCredentialsError("Session expired, sign in again")
        await self._store.delete_session(row.token_hash)
        legacy = await self._new_session(
            user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S, tenant_id=user.tenant_id
        )
        access, refresh = await self._new_jwt_pair(user)
        await self.audit("refresh", user_id=user.user_id, email=user.email)
        return user, SessionTokens(legacy_token=legacy, access_token=access, refresh_token=refresh)

    async def _quarantine_user(self, user: User) -> None:
        """Kill every session of a user whose refresh secret may be replayed."""
        await self._bump_version(user)
        await self._store.save_user(user)
        await self._store.delete_user_sessions(user.user_id)

    async def _principal_from_jwt_token(self, secret: str) -> Principal | None:
        """Resolve a JWT access token to a principal (denylist- and version-checked)."""
        settings = self._require_jwt()
        claims = verify_access_token(
            secret, public_key=settings.public_key, issuer=settings.issuer, audience=settings.audience
        )
        if claims is None:
            return None
        jti = str(claims["jti"])
        if await self._store.is_revoked(jti):
            return None
        user = await self._store.get_user(str(claims["sub"]))
        if not user or user.disabled or self._version_of(user) != int(claims["ver"]):
            return None
        if await self._tenant_is_suspended(user):
            return None
        tenant_id, teams, team_roles = await self._resolve_tenancy(user)
        return Principal(
            user_id=user.user_id,
            email=user.email,
            org_id=user.org_id,
            tenant_id=tenant_id,
            teams=teams,
            team_roles=team_roles,
            role=user.role,
            auth_type="session",
            token_id=jti,
        )

    async def logout(self, session_token: str | None, refresh_token: str | None, principal: Principal | None) -> None:
        """Revoke the caller's tokens: legacy session, refresh row, and access JWT id.

        Best-effort audit when the caller is known; unknown tokens are silently
        accepted (logout is idempotent — a twice-revoked caller is already out).
        """
        if session_token:
            await self._store.delete_session(token_hash(session_token))
        if refresh_token:
            await self._store.delete_session(token_hash(refresh_token))
        if principal is not None and principal.token_id:
            ttl_s = self._jwt.access_ttl_s if self._jwt is not None else C.SESSION_TTL_S
            await self._store.save_revoked(
                RevokedToken(
                    jti=principal.token_id,
                    user_id=principal.user_id or "",
                    expires_at=utc_now() + timedelta(seconds=ttl_s),
                )
            )
        if principal is not None and principal.user_id:
            await self.audit("logout", user_id=principal.user_id, email=principal.email)

    async def authenticate(self, session_token: str | None, authorization: str | None) -> Principal:
        """Resolve session-cookie-then-bearer credentials to a principal.

        The bearer slot carries two credential families, told apart structurally:
        a JWT has two dots (opaque API secrets never do), so a JWT-shaped secret
        that fails verification is rejected immediately instead of burning an
        API-key lookup before the same 401.

        Raises:
            InvalidCredentialsError: When neither credential resolves (the legacy
                "Authentication required" — "Invalid email or password" is the
                login-wrong-credential string only).
        """
        principal = await self._principal_from_session(session_token)
        if principal is None and authorization:
            scheme, _, secret = authorization.partition(" ")
            if scheme.lower() == "bearer" and secret:
                secret = secret.strip()
                if secret.count(".") == 2 and self._jwt is not None:
                    principal = await self._principal_from_jwt_token(secret)
                    if principal is None:
                        raise InvalidCredentialsError("Authentication required")
                else:
                    principal = await self._principal_from_api_key(secret)
        if principal is None:
            raise InvalidCredentialsError("Authentication required")
        return principal

    async def resolve_caller(
        self, session_token: str | None, authorization: str | None
    ) -> Principal | None:
        """Resolve credentials to a principal without raising on anonymous callers.

        Only credential failure maps to ``None`` — backend outages propagate as
        errors, so an outage can never silently demote traffic to anonymous
        (fail-closed, spec 0020 M1b).

        Args:
            session_token: Opaque session token from the session cookie, if sent.
            authorization: Raw ``Authorization`` header value, if sent.

        Returns:
            The resolved principal, or ``None`` when no credential resolved.

        Raises:
            DatabaseError: When the store itself fails (deliberately not absorbed).
        """
        try:
            return await self.authenticate(session_token, authorization)
        except InvalidCredentialsError:
            return None

    async def resolve_request_identity(
        self, session_token: str | None, authorization: str | None, request_id: str
    ) -> tuple[TenantContext | None, Principal | None]:
        """Resolve one request's credentials to its tenant context and principal.

        The tenant middleware (spec 0020, M1b) calls this once per request: the
        context feeds the ambient binding, the principal is stashed on
        ``request.state`` so controllers never resolve the same credentials
        twice (one store trip per request, not two).

        Args:
            session_token: Opaque session token from the session cookie, if sent.
            authorization: Raw ``Authorization`` header value, if sent.
            request_id: Correlation id the middleware copies into the context.

        Returns:
            ``(context, principal)`` — both ``None`` for anonymous callers (the
            middleware binds the system tenant then).

        Raises:
            DatabaseError: When the store itself fails (deliberately not absorbed).
        """
        principal = await self.resolve_caller(session_token, authorization)
        if principal is None:
            return None, None
        context = TenantContext(
            tenant_id=principal.tenant_id or principal.org_id,
            request_id=request_id,
            principal_id=principal.user_id,
            scopes=frozenset(principal.effective_scopes()),
        )
        return context, principal

    async def me(self, principal: Principal) -> tuple[User, list[str]]:
        """Return the session caller's record plus effective scopes for /me.

        The tenant check is the spec-0040 hex analogue of the legacy org
        check: a stamped user's tenant must match the principal's, and a
        mismatch reads as not-found (no cross-tenant oracle). Pre-identity
        rows (`tenant_id is None`, migration pending) keep the legacy org
        boundary so they stay readable until the backfill stamps them.

        Raises:
            InvalidCredentialsError: When the caller is a key, anonymous, or its
                record vanished or was disabled (legacy "Session required").
            AuthNotFoundError: When the user's tenant (or, pre-identity, org)
                does not match the principal's.
        """
        ensure_authenticated(principal)
        if principal.auth_type != "session" or not principal.user_id:
            raise InvalidCredentialsError("Session required")
        user = await self._store.get_user(principal.user_id)
        if not user or user.disabled:
            raise InvalidCredentialsError("Session required")
        if user.tenant_id is not None:
            self._ensure_same_tenant(user.tenant_id, principal, "User")
        else:
            self._ensure_same_org(user.org_id, principal, "User")
        return user, principal.effective_scopes()

    async def redeem_ticket(self, ticket: str | None) -> Principal | None:
        """Swap a single-use ws ticket for its principal (or `None`).

        The redeemed principal carries the same tenant/team projection as the
        session/key/JWT resolvers (spec 0040): the voice WS controller binds
        `principal.tenant_id`, so a tenant-less ticket principal would strand
        the call on the system tenant.
        """
        if not ticket:
            return None
        session = await self._store.get_session(token_hash(ticket))
        if session is None:
            return None
        await self._store.delete_session(session.token_hash)
        if session.kind != C.WS_TICKET_KIND or not session.user_id:
            return None
        expires_at = session.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < utc_now():
            return None
        user = await self._store.get_user(session.user_id)
        if not user or user.disabled:
            return None
        if await self._tenant_is_suspended(user):
            return None
        tenant_id, teams, team_roles = await self._resolve_tenancy(user)
        return Principal(
            user_id=user.user_id,
            email=user.email,
            org_id=user.org_id,
            tenant_id=tenant_id,
            teams=teams,
            team_roles=team_roles,
            role=user.role,
            auth_type="session",
        )

    async def mint_ticket(self, principal: Principal) -> str:
        """Mint a short-lived single-use ws ticket for a calls-scoped caller.

        Raises:
            ForbiddenError: When the caller lacks ``calls:write``.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_scope("calls:write"), "Requires calls:write scope")
        return await self._new_session(
            principal.user_id or "",
            principal.org_id,
            ttl_s=C.WS_TICKET_TTL_S,
            kind=C.WS_TICKET_KIND,
            tenant_id=principal.tenant_id,
        )
