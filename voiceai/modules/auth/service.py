"""Auth service: every auth use-case, no HTTP anywhere (AGENTS.md rule 1e; T2 greenfield).

Greenfield deltas (T2): JWT access tokens (stateless, RS256) plus opaque rotating
refresh rows, dual-written with the legacy Redis sessions until the T7 cutover
retires them; indexed store lookups (no collection scans); `token_version` bumps
on role change, password rotation and logout-all; single-token logout via the
revocation store. Legacy session/ticket/key flows are byte-identical otherwise.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import timedelta, timezone
from typing import Literal

from voiceai.common.datetime_utils import utc_now
from voiceai.common.errors import ConflictError, InvalidRequestError
from voiceai.common.ids import new_id
from voiceai.common.logger import get_logger
from voiceai.modules.auth import constants as C
from voiceai.modules.auth.errors import (
    ForbiddenError,
    InvalidCredentialsError,
    InviteInvalidError,
)
from voiceai.modules.auth.exceptions import (
    ensure_authenticated,
    ensure_found,
    ensure_invite_valid,
    ensure_permitted,
)
from voiceai.modules.auth.models.audit import AuthEvent
from voiceai.modules.auth.models.invite import Invite
from voiceai.modules.auth.models.principal import Principal
from voiceai.modules.auth.models.revoked import RevokedToken
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import User, UserRole
from voiceai.modules.auth.ports import AuthStorePort, LoginLimiter
from voiceai.modules.auth.static_methods import (
    hash_password,
    issue_access_token,
    new_token,
    token_hash,
    verify_access_token,
    verify_password,
)
from voiceai.modules.auth.utils import LocalLoginLimiter

__all__ = ["AuthService", "JwtSettings", "SessionTokens"]

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


class AuthService:
    """Owns signup/login/logout/invites/admin/audit over an injected store.

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
    ) -> str:
        """Mint one session row, returning the RAW token (shown once — rule: secret)."""
        token = new_token()
        await self._store.save_session(
            SessionRecord(
                token_hash=token_hash(token),
                user_id=user_id,
                org_id=org_id,
                kind=kind,
                expires_at=utc_now() + timedelta(seconds=ttl_s),
            )
        )
        return token

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
        return Principal(
            user_id=user.user_id,
            email=user.email,
            org_id=user.org_id,
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
        return Principal(
            user_id=key.created_by,
            email=user.email if user else None,
            org_id=user.org_id if user else "default",
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
        )
        await self._store.save_user(user)
        legacy = await self._new_session(user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S)
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
            user.user_id, user.org_id, ttl_s=C.REMEMBER_TTL_S if remember else C.SESSION_TTL_S
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
        legacy = await self._new_session(user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S)
        access, refresh = await self._new_jwt_pair(user)
        await self.audit("refresh", user_id=user.user_id, email=user.email)
        return user, SessionTokens(legacy_token=legacy, access_token=access, refresh_token=refresh)

    async def _quarantine_user(self, user: User) -> None:
        """Kill every session of a user whose refresh secret may be replayed."""
        await self._bump_version(user)
        await self._store.save_user(user)
        await self._store.delete_user_sessions(user.user_id)

    def _require_jwt(self) -> JwtSettings:
        """Return the JWT configuration, failing loudly when the flows stay dark.

        Raises:
            InvalidRequestError: When no keys are configured — a caller bug or a
                deployment gap, never a store outage.
        """
        if self._jwt is None:
            raise InvalidRequestError("Token pair issuance is not configured")
        return self._jwt

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
        return Principal(
            user_id=user.user_id,
            email=user.email,
            org_id=user.org_id,
            role=user.role,
            auth_type="session",
            token_id=jti,
        )

    async def _bump_version(self, user: User) -> None:
        """Stamp the next revocation version onto a user row (caller persists it).

        Legacy platform rows (quickstart's `RedisStore`, retired at T7) predate the
        stamp: stamping is skipped for them while session sweeps keep revoking, so a
        mixed deployment degrades to sweep-only revocation instead of crashing.
        """
        if "token_version" in type(user).model_fields:
            user.token_version = self._version_of(user) + 1

    @staticmethod
    def _version_of(user: User) -> int:
        """Read a row's revocation stamp, defaulting legacy platform rows to zero."""
        version = getattr(user, "token_version", 0)
        return version if isinstance(version, int) else 0

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

    async def me(self, principal: Principal) -> tuple[User, list[str]]:
        """Return the session caller's record plus effective scopes for /me.

        Raises:
            InvalidCredentialsError: When the caller is a key, anonymous, or its
                record vanished or was disabled (legacy "Session required").
        """
        ensure_authenticated(principal)
        if principal.auth_type != "session" or not principal.user_id:
            raise InvalidCredentialsError("Session required")
        user = await self._store.get_user(principal.user_id)
        if not user or user.disabled:
            raise InvalidCredentialsError("Session required")
        return user, principal.effective_scopes()

    async def redeem_ticket(self, ticket: str | None) -> Principal | None:
        """Swap a single-use ws ticket for its principal (or `None`)."""
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
        return Principal(
            user_id=user.user_id,
            email=user.email,
            org_id=user.org_id,
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
        )

    # -- invites ------------------------------------------------------------

    async def invite(self, principal: Principal, email: str, name: str | None, role: UserRole) -> tuple[Invite, str]:
        """Create an invite; HYBRID delivery — the raw token returns to the caller.

        Raises:
            ForbiddenError: When the caller is not an admin, or grants
                owner/admin without being owner.
            ConflictError: When the email is already registered.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        if role in ("owner", "admin") and principal.role != "owner":
            raise ForbiddenError("Only owners can invite owners or admins")
        if await self._store.get_user_by_email(email.strip().lower()) is not None:
            raise ConflictError("Email already registered")
        token = new_token()
        invite = Invite(
            invite_id=new_id("inv"),
            email=email.strip().lower(),
            name=name,
            role=role,
            token_hash=token_hash(token),
            expires_at=utc_now() + timedelta(seconds=C.INVITE_TTL_S),
            created_by=principal.user_id,
        )
        await self._store.save_invite(invite)
        await self.audit(
            "invite",
            user_id=principal.user_id,
            email=principal.email,
            detail=f"{invite.email} as {invite.role}",
        )
        return invite, token

    async def accept_invite(self, token: str, name: str | None, password: str) -> tuple[User, SessionTokens]:
        """Redeem an invite token into a user plus a first legacy session and JWT pair.

        Raises:
            InviteInvalidError: When no live invite matches the token.
            ConflictError: When the invite email registered meanwhile.
        """
        digest = token_hash(token)
        matched = await self._store.get_invite_by_token_hash(digest)
        if matched is not None and matched.accepted:
            matched = None
        live: Invite = ensure_invite_valid(matched)
        expires_at = live.expires_at
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        if expires_at < utc_now():
            raise InviteInvalidError("Invite invalid or expired")
        if await self._store.get_user_by_email(live.email) is not None:
            raise ConflictError("Email already registered")
        user = User(
            user_id=new_id("usr"),
            email=live.email,
            name=name or live.name,
            password_hash=hash_password(password),
            role=live.role,
        )
        await self._store.save_user(user)
        live.accepted = True
        await self._store.save_invite(live)
        legacy = await self._new_session(user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S)
        access, refresh = await self._new_jwt_pair(user)
        await self.audit("invite_accepted", user_id=user.user_id, email=user.email, detail=f"as {user.role}")
        return user, SessionTokens(legacy_token=legacy, access_token=access, refresh_token=refresh)

    async def list_invites(self, principal: Principal) -> list[Invite]:
        """Return pending invites, token hashes never leaving the store (admins only).

        Raises:
            ForbiddenError: When the caller is not an admin.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        return [i for i in await self._store.list_invites() if not i.accepted]

    async def delete_invite(self, principal: Principal, invite_id: str) -> None:
        """Revoke an invite (admins only).

        Raises:
            ForbiddenError: When the caller is not an admin.
            AuthNotFoundError: When the invite does not exist.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        if not await self._store.delete_invite(invite_id):
            ensure_found(None, "Invite not found")
        await self.audit("invite_revoked", user_id=principal.user_id, email=principal.email, detail=invite_id)

    # -- admin --------------------------------------------------------------

    async def list_users(self, principal: Principal) -> list[User]:
        """Return every user (admins only).

        Raises:
            ForbiddenError: When the caller is not an admin.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        return await self._store.list_users()

    async def _assert_last_owner_safe(self, target: User) -> None:
        """Reject de-powering the last active owner (shared by role/delete)."""
        if target.role != "owner" or target.disabled:
            return
        owners = [u for u in await self._store.list_users() if u.role == "owner" and not u.disabled]
        if len(owners) <= 1:
            raise InvalidRequestError("Cannot remove the last active owner")

    async def _require_owner(self, principal: Principal) -> Principal:
        """Return the acting principal, gated to owners (401 then 403)."""
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("owner"), "Requires owner role or higher")
        return principal

    async def set_user_role(self, principal: Principal, target_id: str, role: UserRole) -> User:
        """Change a user's role, revoking their sessions and access tokens (owners only).

        Raises:
            ForbiddenError: When the caller is not an owner.
            AuthNotFoundError: When the target does not exist.
            InvalidRequestError: On self-edit or last-owner removal.
        """
        actor = await self._require_owner(principal)
        target: User = ensure_found(await self._store.get_user(target_id), "User not found")
        if target.user_id == actor.user_id:
            raise InvalidRequestError("Cannot change your own role")
        await self._assert_last_owner_safe(target)
        target.role = role
        await self._bump_version(target)
        await self._store.save_user(target)
        await self._store.delete_user_sessions(target_id)
        await self.audit(
            "role_change",
            user_id=actor.user_id,
            email=actor.email,
            detail=f"{target.email} -> {role}",
        )
        return target

    async def delete_user(self, principal: Principal, target_id: str) -> None:
        """Delete a user plus all their sessions (owners only).

        Raises:
            ForbiddenError: When the caller is not an owner.
            AuthNotFoundError: When the target does not exist.
            InvalidRequestError: On self-delete or last-owner removal.
        """
        actor = await self._require_owner(principal)
        target: User = ensure_found(await self._store.get_user(target_id), "User not found")
        if target.user_id == actor.user_id:
            raise InvalidRequestError("Cannot delete yourself")
        await self._assert_last_owner_safe(target)
        await self._store.delete_user_sessions(target_id)
        await self._store.delete_user(target_id)
        await self.audit("user_deleted", user_id=actor.user_id, email=actor.email, detail=target.email)

    async def change_password(
        self,
        principal: Principal,
        current_password: str,
        new_password: str,
    ) -> SessionTokens:
        """Rotate a session caller's password, re-issuing their tokens.

        Unlike the legacy keep-current-session sweep, rotation always re-issues:
        the version bump kills every outstanding access token (including the
        caller's, within its short TTL) and every session row dies, so the caller
        leaves with the only live pair. Strictly stronger, and the only sound
        story once access tokens are stateless.

        Raises:
            ForbiddenError: When the caller has no login session.
            InvalidCredentialsError: When the record vanished or the current
                password mismatches (one gate — no user enumeration).
        """
        ensure_authenticated(principal)
        if principal.auth_type != "session" or not principal.user_id:
            raise ForbiddenError("Password change requires a login session")
        user = await self._store.get_user(principal.user_id)
        if not user or not verify_password(current_password, user.password_hash):
            raise InvalidCredentialsError("Current password is incorrect")
        user.password_hash = hash_password(new_password)
        await self._bump_version(user)
        await self._store.save_user(user)
        await self._store.delete_user_sessions(user.user_id)
        legacy = await self._new_session(user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S)
        access, refresh = await self._new_jwt_pair(user)
        await self.audit("password_change", user_id=user.user_id, email=user.email)
        return SessionTokens(legacy_token=legacy, access_token=access, refresh_token=refresh)

    async def auth_events(self, principal: Principal) -> list[AuthEvent]:
        """Return recent audit events, newest first (admins only).

        Raises:
            ForbiddenError: When the caller is not an admin.
        """
        ensure_authenticated(principal)
        ensure_permitted(principal.has_role("admin"), "Requires admin role or higher")
        return await self._store.list_auth_events()

    async def audit(
        self,
        event_type: str,
        *,
        user_id: str | None = None,
        email: str | None = None,
        detail: str | None = None,
    ) -> None:
        """Append one audit row; NEVER fails the operation it records."""
        try:
            await self._store.add_auth_event(
                AuthEvent(
                    event_id=new_id("evt"),
                    type=event_type,
                    user_id=user_id,
                    email=email,
                    detail=detail,
                    created_at=utc_now(),
                )
            )
        except Exception:  # noqa: BLE001 - audit is advisory by design
            logger.warning("auth audit write failed", exc_info=True)
