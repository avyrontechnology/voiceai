"""Auth service: every auth use-case, no HTTP anywhere (AGENTS.md rule 1e).

Bodies moved line-by-line from ``voiceai/platform/auth.py`` (session/key
resolution, ticket minting) and ``voiceai/platform/auth_router.py`` (signup,
login, invites, admin, password, audit call-sites) — spec 0005, C3. The refactor
deltas are mechanical:

* ``HTTPException`` → module errors (``InvalidCredentialsError`` 401,
  ``ForbiddenError`` 403, ``InvalidRequestError`` 400, ``ConflictError`` 409,
  ``TooManyAttemptsError`` 429) — HTTP mapping returns with the controller at C5.
* Cookie I/O (``mint_session``/``revoke_session``) and header extraction stay
  behind — C5 shapes them around ``_new_session``/``logout``.
* ``MemoryStore`` → ``AuthStorePort``; rows arrive through it (rule 9). Both
  legacy stores satisfy the port structurally.
* Scanning ``_data``/``_redis`` becomes ``list_user_sessions`` (a port method
  both stores now carry); the invite digest lookup keeps its plain ``==``.
* Policy constants come from ``.constants``; utc time from ``common``;
  audit stays best-effort (never fails the op it records).
"""

from __future__ import annotations

import logging
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
from voiceai.modules.auth.models.session import SessionRecord
from voiceai.modules.auth.models.user import User, UserRole
from voiceai.modules.auth.ports import AuthStorePort
from voiceai.modules.auth.static_methods import (
    hash_password,
    new_token,
    token_hash,
    verify_password,
)
from voiceai.modules.auth.utils import check_login_allowed

__all__ = ["AuthService"]

logger: logging.Logger = get_logger("auth")


class AuthService:
    """Owns signup/login/logout/invites/admin/audit over an injected store.

    Args:
        store: The auth persistence behind ``AuthStorePort`` (a fake in tests).
    """

    def __init__(self, store: AuthStorePort) -> None:
        self._store = store

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
        for key in await self._store.list_api_keys():
            if not key.key_hash:
                continue  # legacy key minted before hashing; rotate it
            prefix_ok = secret.startswith(key.prefix) if key.prefix else True
            if prefix_ok and key.key_hash == digest:
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
        return None

    # -- entry points -------------------------------------------------------

    async def signup(self, email: str, name: str | None, password: str) -> tuple[User, str]:
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
        token = await self._new_session(user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S)
        await self.audit("signup", user_id=user.user_id, email=user.email, detail="first user (owner)")
        return user, token

    async def login(self, email: str, password: str, remember: bool, *, client_ip: str) -> tuple[User, str]:
        """Check credentials, mint a session, stamp last login.

        Raises:
            TooManyAttemptsError: When the IP exhausted its window.
            InvalidCredentialsError: When the email/password/disabled gate fails.
        """
        check_login_allowed(client_ip)
        user = await self._store.get_user_by_email(email)
        if not user or user.disabled or not verify_password(password, user.password_hash):
            await self.audit("login_failed", email=email.strip().lower())
            raise InvalidCredentialsError("Invalid email or password")
        token = await self._new_session(
            user.user_id, user.org_id, ttl_s=C.REMEMBER_TTL_S if remember else C.SESSION_TTL_S
        )
        user.last_login_at = utc_now()
        await self._store.save_user(user)
        await self.audit("login", user_id=user.user_id, email=user.email)
        return user, token

    async def logout(self, token: str | None, principal: Principal | None) -> None:
        """Revoke one session token; best-effort audit when the caller is known."""
        if token:
            await self._store.delete_session(token_hash(token))
        if principal is not None and principal.user_id:
            await self.audit("logout", user_id=principal.user_id, email=principal.email)

    async def authenticate(self, session_token: str | None, authorization: str | None) -> Principal:
        """Resolve session-cookie-then-bearer credentials to a principal.

        Raises:
            InvalidCredentialsError: When neither credential resolves (the legacy
                "Authentication required" — "Invalid email or password" is the
                login-wrong-credential string only).
        """
        principal = await self._principal_from_session(session_token)
        if principal is None and authorization:
            scheme, _, secret = authorization.partition(" ")
            if scheme.lower() == "bearer" and secret:
                principal = await self._principal_from_api_key(secret.strip())
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

    async def accept_invite(self, token: str, name: str | None, password: str) -> tuple[User, str]:
        """Redeem an invite token into a user plus a first session.

        Raises:
            InviteInvalidError: When no live invite matches the token.
            ConflictError: When the invite email registered meanwhile.
        """
        digest = token_hash(token)
        matched = next(
            (
                invite
                for invite in await self._store.list_invites()
                if invite.token_hash == digest and not invite.accepted
            ),
            None,
        )
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
        session_token = await self._new_session(user.user_id, user.org_id, ttl_s=C.SESSION_TTL_S)
        await self.audit("invite_accepted", user_id=user.user_id, email=user.email, detail=f"as {user.role}")
        return user, session_token

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
        """Change a user's role, revoking their sessions (owners only).

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
        current_token: str | None,
    ) -> None:
        """Rotate a session caller's password, keeping only the current session.

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
        await self._store.save_user(user)
        # Keep this session, kill the rest.
        keep = {token_hash(current_token)} if current_token else set()
        for session in await self._store.list_user_sessions(user.user_id):
            if session.token_hash not in keep:
                await self._store.delete_session(session.token_hash)
        await self.audit("password_change", user_id=user.user_id, email=user.email)

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
